"""Budgeted document search; widgets and cursors stay on the UI thread."""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import deque
from time import perf_counter

from PySide6.QtCore import QObject, QEvent, QPoint, QTimer, Signal
from PySide6.QtGui import QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QTextEdit

from config.runtime import RX_SEARCH_MAX_MATCHES, SEARCH_DEBOUNCE_INTERVAL_MS, SEARCH_SLICE_BUDGET_MS
from core.text_matching import SearchPattern


class DocumentSearch(QObject):
    updated = Signal()
    paint_requested = Signal()
    activated = Signal()

    def __init__(self, editor, *, qt: bool):
        super().__init__(editor)
        self.editor = editor
        self.doc = editor.document()
        self.qt = qt
        self.matches = []
        self.limited = False
        self.error = ""
        self.busy = False
        self._key = None
        self._pattern = None
        self._dirty = 0
        self._version = 0
        self._task = None
        self._task_dirty = None
        self._ends = []
        self._snapshot = None
        self._snapshot_version = -1
        self._refresh = QTimer(self)
        self._refresh.setSingleShot(True)
        self._refresh.setInterval(SEARCH_DEBOUNCE_INTERVAL_MS)
        self._refresh.timeout.connect(self._begin)
        self._step_timer = QTimer(self)
        self._step_timer.setSingleShot(True)
        self._step_timer.timeout.connect(self._step)
        self._paint_timer = QTimer(self)
        self._paint_timer.setSingleShot(True)
        self._paint_timer.timeout.connect(self.paint_requested)
        self.doc.contentsChange.connect(self._changed)
        editor.verticalScrollBar().valueChanged.connect(self.repaint)
        editor.horizontalScrollBar().valueChanged.connect(self.repaint)
        editor.viewport().installEventFilter(self)
        editor.installEventFilter(self)

    def eventFilter(self, obj, event):
        # Qt may deliver destruction events after Python has cleared a wrapper.
        if not hasattr(self, "editor"):
            return False
        if obj is self.editor and event.type() == QEvent.Type.Hide:
            self.clear()
        elif obj is self.editor and event.type() == QEvent.Type.Show:
            QTimer.singleShot(0, self.activated.emit)
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.FontChange, QEvent.Type.PaletteChange):
            self.repaint()
        return False

    def repaint(self, *_args):
        if not self._paint_timer.isActive():
            self._paint_timer.start(0)

    def clear(self):
        self._refresh.stop()
        self._step_timer.stop()
        self._task = None
        self._key = None
        self._pattern = None
        self._dirty = 0
        self._snapshot = None
        self.matches = []
        self._ends = []
        self.limited = self.busy = False
        self.error = ""
        self.updated.emit()

    def search(self, query, regex, case, word, enabled=True):
        if not enabled or not query:
            self.clear()
            return
        key = (query, regex, case, word)
        if key != self._key:
            self.clear()
            self._key = key
            self._pattern = SearchPattern(*key, qt=self.qt)
            self.error = self._pattern.error
            self.updated.emit()
        if self.error:
            return
        if self._task is None and self._dirty is not None:
            self._refresh.stop()
            self._begin()
        else:
            self.repaint()

    def schedule(self):
        if self._key and self.editor.isVisible() and not self.error and not self._refresh.isActive():
            self._refresh.start()

    def _changed(self, position, removed, added):
        if not removed and not added:
            return
        self._version += 1
        self._snapshot = None
        block = self.doc.findBlock(min(position, self.doc.characterCount() - 1))
        dirty = block.position() if block.isValid() else 0
        self._dirty = dirty if self._dirty is None else min(dirty, self._dirty)
        if self._task is not None:
            self._task_append_only &= removed == 0 and position >= self._task_length
            self._task_dirty = dirty if self._task_dirty is None else min(dirty, self._task_dirty)
            if removed:
                # A cursor in a suspended chunk can now be beyond the new end.
                # Restart on deletion/overwrite instead of reading invalid ranges.
                self._task = None
                self._step_timer.stop()
                self.busy = False
                self._dirty = 0
        # Positions before the changed paragraph remain valid. Drop stale tail
        # ranges immediately so navigation cannot select text that has been erased.
        index = bisect_right(self._ends, dirty)
        if index < len(self.matches):
            self.matches = self.matches[:index]
            self._ends = self._ends[:index]
            self.updated.emit()
        self.schedule()
        self.repaint()

    def _begin(self):
        if self._task is not None or self._dirty is None or not self._pattern or self.error:
            return
        self.busy = True
        self._task_dirty = None
        self._task_version = self._version
        self._task_length = self.doc.characterCount() - 1
        self._task_append_only = True
        # A capped cache may lack older matches needed after an edit. Refill it
        # from the end instead of incorrectly treating its retained prefix as complete.
        self._from = 0 if self.limited or self._pattern.multiline else self._dirty
        self._base = [span for span in self.matches if span[1] <= self._from]
        self._dirty = None
        self._found = deque(maxlen=RX_SEARCH_MAX_MATCHES + 1)
        self._task = self._scan()
        self.updated.emit()
        self._step_timer.start(0)

    def _scan(self):
        if self._pattern.multiline:
            if self._snapshot is None or self._snapshot_version != self._version:
                parts = []
                start = 0
                length = self.doc.characterCount() - 1
                while start < length:
                    end = min(length, start + 64 * 1024)
                    if end < length and 0xDC00 <= ord(self.doc.characterAt(end)) <= 0xDFFF:
                        end -= 1
                    cursor = QTextCursor(self.doc)
                    cursor.setPosition(start)
                    cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
                    parts.append(cursor.selectedText().replace("\u2029", "\n").replace("\u2028", "\n").replace("\u00a0", " "))
                    start = end
                    yield None
                    if self._task_version != self._version and not self._task_append_only:
                        return
                if self._task_version != self._version and not self._task_append_only:
                    return
                self._snapshot = "".join(parts)
                self._snapshot_version = self._task_version
            # shortcut: a single regex call cannot be preempted. Hard timeouts
            # require process isolation; the timer only budgets between matches.
            for span in self._pattern.spans(self._snapshot):
                self._found.append(span)
                yield None
            return
        if not self.qt:
            # Python literal matching is non-overlapping and forward. Preserve
            # that phase across chunks (e.g. "aa" in a long run of "a").
            length = self.doc.characterCount() - 1
            padding = len(self._pattern.query.encode("utf-16-le", errors="surrogatepass")) // 2 + 2
            start = self._from
            while start < length:
                end = min(length, start + 64 * 1024)
                if end < length and 0xDC00 <= ord(self.doc.characterAt(end)) <= 0xDFFF:
                    end -= 1
                lo, hi = max(0, start - 2), min(length, end + padding)
                if lo and 0xDC00 <= ord(self.doc.characterAt(lo)) <= 0xDFFF:
                    lo -= 1
                if hi < length and 0xDC00 <= ord(self.doc.characterAt(hi)) <= 0xDFFF:
                    hi += 1
                cursor = QTextCursor(self.doc)
                cursor.setPosition(lo)
                cursor.setPosition(hi, QTextCursor.MoveMode.KeepAnchor)
                text = cursor.selectedText().replace("\u2029", "\n").replace("\u2028", "\n").replace("\u00a0", " ")
                offset = units = 0
                while units < start - lo and offset < len(text):
                    units += 2 if ord(text[offset]) > 0xffff else 1
                    offset += 1
                next_start = end
                for begin, finish in self._pattern.spans(text, offset):
                    if lo + begin >= end:
                        break
                    self._found.append((lo + begin, lo + finish))
                    next_start = max(next_start, lo + finish)
                    yield None
                start = next_start
                yield None
            return
        if not self._pattern.regex and "\n" not in self._pattern.query:
            # Bounded copies also handle one enormous line. Include query-sized
            # context on both sides so chunk edges cannot change whole-word hits.
            length = self.doc.characterCount() - 1
            padding = len(self._pattern.query.encode("utf-16-le", errors="surrogatepass")) // 2 + 2
            end = length
            while end > self._from:
                start = max(self._from, end - 64 * 1024)
                if start and 0xDC00 <= ord(self.doc.characterAt(start)) <= 0xDFFF:
                    start -= 1
                lo, hi = max(0, start - padding), min(length, end + padding)
                if lo and 0xDC00 <= ord(self.doc.characterAt(lo)) <= 0xDFFF:
                    lo -= 1
                if hi < length and 0xDC00 <= ord(self.doc.characterAt(hi)) <= 0xDFFF:
                    hi += 1
                cursor = QTextCursor(self.doc)
                cursor.setPosition(lo)
                cursor.setPosition(hi, QTextCursor.MoveMode.KeepAnchor)
                text = cursor.selectedText().replace("\u2029", "\n").replace("\u00a0", " ")
                spans = deque(maxlen=RX_SEARCH_MAX_MATCHES + 1)
                for begin, finish in self._pattern.spans(text):
                    if start <= lo + begin < end:
                        spans.append((lo + begin, lo + finish))
                    yield None
                remaining = RX_SEARCH_MAX_MATCHES + 1 - len(self._found)
                self._found.extendleft(reversed(list(spans)[-remaining:]))
                if len(self._found) > RX_SEARCH_MAX_MATCHES:
                    return
                end = start
                yield None
            return
        block = self.doc.lastBlock()
        while block.isValid() and block.position() >= self._from:
            position = block.position()
            spans = deque(maxlen=RX_SEARCH_MAX_MATCHES + 1)
            for start, end in self._pattern.spans(block.text()):
                spans.append((position + start, position + end))
                yield None
            remaining = RX_SEARCH_MAX_MATCHES + 1 - len(self._found)
            self._found.extendleft(reversed(list(spans)[-remaining:]))
            # _found is ascending. Keep the newest results, even on very long lines.
            if len(self._found) > RX_SEARCH_MAX_MATCHES:
                return
            block = block.previous()
            yield None

    def _step(self):
        if self._task is None:
            return
        deadline = perf_counter() + SEARCH_SLICE_BUDGET_MS / 1000
        try:
            while perf_counter() < deadline:
                next(self._task)
        except StopIteration:
            self._task = None
            if self._pattern.multiline and self._task_version != self._version and not self._task_append_only:
                self._dirty = 0
            else:
                spans = self._base + list(self._found)
                self.limited = len(spans) > RX_SEARCH_MAX_MATCHES
                spans = spans[-RX_SEARCH_MAX_MATCHES:]
                if self._task_dirty is not None:
                    spans = [span for span in spans if span[1] <= self._task_dirty]
                self.matches = spans
                self._ends = [end for _, end in spans]
            self.busy = False
            self.updated.emit()
            if self._dirty is not None:
                self.schedule()
            return
        self._step_timer.start(1)

    @property
    def updating(self):
        return self.busy or (self._key is not None and self._dirty is not None and not self.error)

    def selections(self, color):
        if not self.matches or not self.editor.isVisible():
            return []
        viewport = self.editor.viewport()
        start = max(0, self.editor.cursorForPosition(QPoint(0, 0)).position() - 512)
        end = self.editor.cursorForPosition(QPoint(viewport.width(), viewport.height())).position() + 512
        first = bisect_left(self._ends, start)
        last = bisect_right(self.matches, (end, self.doc.characterCount()))
        fmt = QTextCharFormat()
        fmt.setBackground(color)
        extras = []
        for begin, finish in self.matches[first:last]:
            extra = QTextEdit.ExtraSelection()
            extra.format = fmt
            cursor = QTextCursor(self.doc)
            cursor.setPosition(begin)
            cursor.setPosition(finish, QTextCursor.MoveMode.KeepAnchor)
            extra.cursor = cursor
            extras.append(extra)
        return extras
