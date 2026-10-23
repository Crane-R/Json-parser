import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Match:
    start: int
    end: int


class TextSearch:
    def __init__(self, text: str, term: str, case_sensitive: bool = False):
        self.text = text
        self.term = term
        self.pattern = re.compile(re.escape(term), 0 if case_sensitive else re.IGNORECASE) if term else None
        self.total = sum(1 for unused in self.pattern.finditer(text)) if self.pattern else 0
        self.current: Match | None = None
        self.ordinal = 0

    def ranges(self, limit=2000):
        if self.pattern:
            for index, match in enumerate(self.pattern.finditer(self.text)):
                if index >= limit:
                    break
                yield Match(match.start(), match.end())

    def select_after(self, offset=0):
        if not self.total:
            return None
        found = self.pattern.search(self.text, offset) or self.pattern.search(self.text)
        self.current = Match(found.start(), found.end())
        self.ordinal = sum(1 for match in self.pattern.finditer(self.text, 0, found.start())) + 1
        return self.current

    def next(self):
        return self.select_after(self.current.end if self.current else 0)

    def previous(self):
        if not self.total:
            return None
        target = self.ordinal - 1 if self.ordinal > 1 else self.total
        for index, found in enumerate(self.pattern.finditer(self.text), 1):
            if index == target:
                self.ordinal = index
                self.current = Match(found.start(), found.end())
                return self.current

    def replacement(self, replacement: str, all_matches: bool = False):
        if not self.total:
            return self.text, 0
        if all_matches:
            return self.pattern.subn(lambda unused: replacement, self.text)
        if self.current is None:
            self.next()
        span = self.current
        return self.text[:span.start] + replacement + self.text[span.end:], 1

    def tk_index(self, offset: int):
        return f"1.0+{offset}c"
