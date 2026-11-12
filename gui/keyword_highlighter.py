"""Visible keyword foregrounds, independent of document text and ANSI formats."""
from __future__ import annotations

from collections import OrderedDict
import re
from time import perf_counter

from PySide6.QtCore import QObject, QEvent, QPoint, QTimer
from PySide6.QtGui import QTextFormat, QTextLayout, QTextCursor

from config.runtime import KEYWORD_SLICE_BUDGET_MS, KEYWORD_FORMAT_CACHE_LIMIT
from core.text_matching import utf16_positions

# Only received bodies participate; timestamps, echo and system messages do not.
RX_TEXT_KIND = int(QTextFormat.Property.UserProperty) + 1
RX_PLAIN = 1
RX_ANSI = 2


class KeywordHighlighter(QObject):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor
        self.rules = []
        self._patterns = []
        self._padding = 512
        self._rule_key = None
        self._cache = OrderedDict()
        self._format_cache = OrderedDict()
        self._painted = []
        self._task = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._step)
        editor.document().contentsChange.connect(self.refresh)
        editor.verticalScrollBar().valueChanged.connect(self.refresh)
        editor.horizontalScrollBar().valueChanged.connect(self.refresh)
        editor.viewport().installEventFilter(self)
        editor.installEventFilter(self)

    def eventFilter(self, obj, event):
        # Qt may deliver destruction events after Python has cleared a wrapper.
        if not hasattr(self, "editor"):
            return False
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.Hide, QEvent.Type.FontChange):
            self.refresh()
        return False

    def set_rules(self, rules):
        key = tuple((rule['enabled'], tuple((p.pattern, p.flags) for p in rule['patterns'])) for rule in rules)
        if key != self._rule_key:
            self._cache.clear()
            self._rule_key = key
        self.rules = rules
        self._format_cache.clear()
        self._padding = 512 + max((2 * len(p.pattern) for rule in rules for p in rule['patterns']), default=0)
        self._patterns = []
        for index, rule in enumerate(rules):
            if not rule['enabled']:
                continue
            for pattern, literal in zip(rule['patterns'], rule['literals']):
                insensitive = bool(pattern.flags & re.IGNORECASE)
                if insensitive:
                    literal = literal.lower() if literal.isascii() else None
                self._patterns.append((index, pattern, literal, insensitive))
        self.refresh()

    def refresh(self, *_args):
        self._task = None
        if not self._painted and not self._patterns:
            return
        if not self._timer.isActive():
            self._timer.start(0)

    def _visible(self):
        viewport = self.editor.viewport()
        first = self.editor.cursorForPosition(QPoint(0, 0)).block()
        last = self.editor.cursorForPosition(QPoint(viewport.width(), viewport.height())).block()
        for _ in range(2):
            if first.previous().isValid():
                first = first.previous()
            if last.next().isValid():
                last = last.next()
        blocks = []
        while first.isValid() and first.position() <= last.position():
            blocks.append(first)
            first = first.next()
        return blocks

    def _paint(self):
        blocks = self._visible() if self._patterns and self.editor.isVisible() else []
        viewport = self.editor.viewport()
        padding = self._padding
        visible_start = self.editor.cursorForPosition(QPoint(0, 0)).position() - padding
        visible_end = self.editor.cursorForPosition(QPoint(viewport.width(), viewport.height())).position() + padding
        for anchor in self._painted:
            old = anchor.block()
            if old.isValid() and old not in blocks:
                old.layout().clearFormats()
        # QTextBlock.isValid() can remain true after clear() frees its storage.
        # Cursors track document edits; only resolve their current blocks here.
        self._painted = [QTextCursor(block) for block in blocks]
        for block in blocks:
            revision = block.revision()
            fragments = []
            iterator = block.begin()
            while not iterator.atEnd():
                fragment = iterator.fragment()
                kind = fragment.charFormat().property(RX_TEXT_KIND)
                start = max(fragment.position(), visible_start)
                end = min(fragment.position() + fragment.length(), visible_end)
                if end > start and kind in (RX_PLAIN, RX_ANSI):
                    if start == fragment.position() and end == fragment.position() + fragment.length():
                        text = fragment.text()
                    else:
                        # Keep Qt's UTF-16 slicing for clipped long lines.
                        cursor = QTextCursor(self.editor.document())
                        cursor.setPosition(start)
                        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
                        text = cursor.selectedText()
                    fragments.append((start - block.position(), text, kind))
                iterator += 1
            key = tuple(fragments)
            cached = self._cache.get(key)
            if cached is None:
                runs = []
                plain = []
                for position, text, kind in fragments:
                    length = len(text.encode('utf-16-le', errors='surrogatepass')) // 2
                    if kind == RX_PLAIN:
                        plain.append((position, position + length))
                    if kind in (RX_PLAIN, RX_ANSI):
                        if runs and runs[-1][2] == position:
                            runs[-1] = (runs[-1][0], runs[-1][1] + text, position + length)
                        else:
                            runs.append((position, text, position + length))
                matches = []
                for position, text, _end in runs:
                    convert = utf16_positions(text)
                    lower_text = text.lower() if text.isascii() else None
                    for number, (index, pattern, literal, insensitive) in enumerate(self._patterns):
                        if number % 32 == 0:
                            yield
                        candidate = lower_text if insensitive else text
                        # Only reject certain misses; preserve Unicode regex case/word rules.
                        if literal is not None and candidate is not None and literal not in candidate:
                            continue
                        for match_number, match in enumerate(pattern.finditer(text)):
                            matches.append((position + convert(match.start()), position + convert(match.end()), index))
                            if match_number % 32 == 31:
                                yield
                matches.sort(key=lambda span: (span[0], -(span[1] - span[0]), span[2]))
                cached = []
                previous_end = -1
                for match_number, (start, end, index) in enumerate(matches):
                    if match_number % 32 == 0:
                        yield
                    if start < previous_end:
                        continue
                    previous_end = end
                    # ANSI retains its format even when a keyword straddles it.
                    for lo, hi in plain:
                        begin, finish = max(lo, start), min(hi, end)
                        if finish > begin:
                            cached.append((begin, finish, index))
                self._cache[key] = cached
                if len(self._cache) > 256:
                    self._cache.popitem(last=False)
            else:
                self._cache.move_to_end(key)
            if not block.isValid() or block.revision() != revision:
                continue
            # Building many Qt ranges must yield too; apply each block atomically.
            formats = []
            for format_number, (start, end, index) in enumerate(cached):
                if format_number % 32 == 0:
                    yield
                format_key = (start, end, index)
                item = self._format_cache.get(format_key)
                if item is None:
                    item = QTextLayout.FormatRange()
                    item.start, item.length = start, end - start
                    item.format = self.rules[index]['fmt']
                    self._format_cache[format_key] = item
                    if len(self._format_cache) > KEYWORD_FORMAT_CACHE_LIMIT:
                        self._format_cache.popitem(last=False)
                else:
                    self._format_cache.move_to_end(format_key)
                formats.append(item)
            if not block.isValid() or block.revision() != revision:
                continue
            block.layout().setFormats(formats)
            self.editor.viewport().update()
            yield
        self.editor.viewport().update()

    def _step(self):
        if self._task is None:
            self._task = self._paint()
        task = self._task
        deadline = perf_counter() + KEYWORD_SLICE_BUDGET_MS / 1000
        try:
            # Qt layout can synchronously refresh and invalidate this task.
            while self._task is task and perf_counter() < deadline:
                next(task)
        except StopIteration:
            if self._task is task:
                self._task = None
            return
        if self._task is task:
            self._timer.start(1)
