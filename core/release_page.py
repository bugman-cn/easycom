"""Read release notes and per-asset digests from public GitHub Release pages."""
from html.parser import HTMLParser


class ReleaseNotes(HTMLParser):
    def __init__(self):
        super().__init__()
        self.depth = 0
        self.found_body = False
        self.asset_fragment = ""
        self.text = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if not self.depth:
            if tag == "include-fragment" and "/releases/expanded_assets/" in attrs.get("src", ""):
                self.asset_fragment = attrs["src"]
            if tag == "div" and attrs.get("data-test-selector") == "body-content":
                self.found_body = True
                self.depth = 1
            return
        if tag == "div":
            self.depth += 1
        elif tag in ("br", "p", "pre", "h1", "h2", "h3", "h4", "tr", "li"):
            self.text.append("\n")
            if tag == "li":
                self.text.append("• ")
        elif tag == "img" and attrs.get("alt"):
            self.text.append(attrs["alt"])

    def handle_endtag(self, tag):
        if self.depth and tag == "div":
            self.depth -= 1

    def handle_data(self, data):
        if self.depth:
            self.text.append(data)

    def plain_text(self):
        return "\n".join(line.rstrip() for line in "".join(self.text).strip().splitlines())


class AssetDigests(HTMLParser):
    def __init__(self, asset_path):
        super().__init__()
        self.asset_path = asset_path
        self.matched = False
        self.digest = ""
        self.matches = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "li":
            self.matched = False
            self.digest = ""
        elif tag == "a" and attrs.get("href") == self.asset_path:
            self.matched = True
        elif tag == "clipboard-copy" and attrs.get("value", "").startswith("sha256:"):
            self.digest = attrs["value"]

    def handle_endtag(self, tag):
        if tag == "li" and self.matched:
            self.matches.append(self.digest)
            self.matched = False
