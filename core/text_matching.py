"""Search patterns and Python/Qt position conversion, without widget access."""
from __future__ import annotations

from bisect import bisect_left
import re

from PySide6.QtCore import QRegularExpression

_NON_BMP = re.compile(r"[^\u0000-\uffff]")


def utf16_positions(text: str):
    """Convert Python indices to QTextCursor positions without copying the text."""
    extra = [m.start() for m in _NON_BMP.finditer(text)]
    return lambda index: index + bisect_left(extra, index)


class SearchPattern:
    def __init__(self, query: str, regex: bool, case: bool, word: bool, *, qt: bool):
        self.query, self.regex, self.case, self.word, self.qt = query, regex, case, word, qt
        self.error = ""
        self.pattern = None
        # Python regex may span paragraphs via lookaround, classes or inline flags.
        # Its context cannot safely be inferred from the spelling of the pattern.
        self.multiline = not qt and (regex or "\n" in query)
        if not query:
            return
        if qt:
            source = query if regex else QRegularExpression.escape(query)
            if word:
                if regex:
                    source = r"\b(?:" + source + r")\b"
                source = r"(?<![\p{L}\p{N}])(?:" + source + r")(?![\p{L}\p{N}])"
            # Qt's backward literal search includes overlapping hits.
            self.capture = 0 if regex else 1
            if not regex:
                source = "(?=(" + source + "))"
            self.pattern = QRegularExpression(source)
            if not case:
                self.pattern.setPatternOptions(QRegularExpression.PatternOption.CaseInsensitiveOption)
            if not self.pattern.isValid():
                self.error = self.pattern.errorString()
            else:
                self.pattern.optimize()
        else:
            source = query if regex else re.escape(query)
            try:
                flags = 0 if case else re.IGNORECASE
                if word:
                    # Preserve global flags at the beginning of Python patterns.
                    prefix = re.match(r"^(?:\(\?[aiLmsux]+\))*", source).group()
                    source = prefix + r"\b(?:" + source[len(prefix):] + r")\b"
                self.pattern = re.compile(source, flags)
            except re.error as error:
                self.error = str(error)

    def spans(self, text: str, offset: int = 0):
        """Yield UTF-16 ranges in document order; skip empty matches."""
        if not self.query or self.error:
            return
        if not self.regex and self.case:
            convert = utf16_positions(text)
            while (start := text.find(self.query, offset)) >= 0:
                end = start + len(self.query)
                if self.qt:
                    valid = not self.word or (
                        (start == 0 or not text[start - 1].isalnum())
                        and (end == len(text) or not text[end].isalnum())
                    )
                else:
                    valid = not self.word or self.pattern.match(text, start) is not None
                if valid:
                    yield convert(start), convert(end)
                offset = start + 1 if self.qt else end
            return
        if self.qt:
            matches = self.pattern.globalMatch(text)
            while matches.hasNext():
                match = matches.next()
                start, end = match.capturedStart(self.capture), match.capturedEnd(self.capture)
                if end > start:
                    yield start, end
        else:
            convert = utf16_positions(text)
            for match in self.pattern.finditer(text, offset):
                if match.end() > match.start():
                    yield convert(match.start()), convert(match.end())
