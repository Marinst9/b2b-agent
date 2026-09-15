"""Minimal, dependency-free HTML helpers for the research crawler.

Uses only the stdlib parser so we don't add a new third-party dependency for
what is a small, bounded task (extract same-site links, extract visible text).
"""
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

_SKIP_TEXT_TAGS = {"script", "style", "noscript", "template"}


class _LinkExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            for name, value in attrs:
                if name == "href" and value:
                    self.links.append(value)


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self._skip_depth = 0
        self.chunks = []

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TEXT_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in _SKIP_TEXT_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth == 0 and data.strip():
            self.chunks.append(data.strip())


def extract_links(html: str, base_url: str) -> list[str]:
    parser = _LinkExtractor()
    try:
        parser.feed(html)
    except Exception:
        return []
    absolute = []
    for href in parser.links:
        if href.startswith("mailto:") or href.startswith("tel:") or href.startswith("javascript:"):
            continue
        absolute.append(urljoin(base_url, href))
    return absolute


def extract_visible_text(html: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(html)
    except Exception:
        return html
    return "\n".join(parser.chunks)


def same_site(url_a: str, url_b: str) -> bool:
    def norm(host):
        host = (host or "").lower()
        return host[4:] if host.startswith("www.") else host

    return norm(urlsplit(url_a).hostname) == norm(urlsplit(url_b).hostname)
