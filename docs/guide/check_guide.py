#!/usr/bin/env python3
"""Test for the Gemstone Guide (docs/guide). Run: python3 docs/guide/check_guide.py

Fails (exit 1) when any of these is violated:
  1. every expected page exists;
  2. every page parses into a well-nested element tree;
  3. every relative href/src resolves to a file, and every #fragment to an id in its target;
  4. every page loads assets/style.css and assets/site.js, and nothing else external except
     Google Fonts;
  5. <html> carries data-title-en and data-title-ko;
  6. bilingual completeness: each data-lang="en" element is immediately followed by a sibling
     data-lang="ko" element of the same tag, both non-empty, and vice versa;
  7. no visible text outside a data-lang element, except inside pre/code/script/style or an
     element marked data-i18n="none" (brand names, symbols).

What this cannot catch: a Korean string that is not a translation of its English twin, layout
problems, or broken external links.
"""
from __future__ import annotations

import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

GUIDE = Path(__file__).resolve().parent
PAGES = [
    "index.html", "getting-started.html", "concepts.html",
    "guide-server.html", "guide-clients.html", "guide-protocol.html", "guide-tools.html",
    "ecosystem.html", "faq.html",
]
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source",
        "track", "wbr", "path", "circle", "rect", "line", "polyline", "polygon", "ellipse",
        "stop", "use"}
SKIP_TEXT = {"pre", "code", "script", "style", "title"}
ALLOWED_EXTERNAL = ("https://fonts.googleapis.com/", "https://fonts.gstatic.com/")


class Node:
    def __init__(self, tag, attrs, parent):
        self.tag, self.attrs, self.parent = tag, dict(attrs), parent
        self.children: list[Node | str] = []

    def elements(self):
        return [c for c in self.children if isinstance(c, Node)]

    def text(self):
        return "".join(c if isinstance(c, str) else c.text() for c in self.children)


class TreeBuilder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root", [], None)
        self.cur = self.root
        self.errors: list[str] = []

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs, self.cur)
        self.cur.children.append(node)
        if tag not in VOID:
            self.cur = node

    def handle_startendtag(self, tag, attrs):
        self.cur.children.append(Node(tag, attrs, self.cur))

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if self.cur.tag != tag:
            line, _ = self.getpos()
            self.errors.append(f"line {line}: </{tag}> closes <{self.cur.tag}>")
            n = self.cur
            while n is not None and n.tag != tag:
                n = n.parent
            if n is None:
                return
            self.cur = n
        self.cur = self.cur.parent

    def handle_data(self, data):
        self.cur.children.append(data)


def walk(node):
    for c in node.elements():
        yield c
        yield from walk(c)


def check_page(name: str, ids: dict[str, set[str]], problems: list[str]):
    path = GUIDE / name
    src = path.read_text(encoding="utf-8")
    if "\r\n" in src:
        problems.append(f"{name}: CRLF line endings")
    tb = TreeBuilder()
    tb.feed(src)
    tb.close()
    for e in tb.errors:
        problems.append(f"{name}: nesting {e}")
    if tb.cur is not tb.root:
        problems.append(f"{name}: unclosed <{tb.cur.tag}>")

    nodes = list(walk(tb.root))
    html = next((n for n in nodes if n.tag == "html"), None)
    if html is None:
        problems.append(f"{name}: no <html>")
        return
    for lang in ("en", "ko"):
        if not html.attrs.get(f"data-title-{lang}", "").strip():
            problems.append(f"{name}: <html> missing data-title-{lang}")

    # 3 + 4: links and resources
    has_css = has_js = False
    for n in nodes:
        for attr in ("href", "src"):
            ref = n.attrs.get(attr)
            if not ref:
                continue
            if n.tag == "link" and ref == "assets/style.css":
                has_css = True
            if n.tag == "script" and ref == "assets/site.js":
                has_js = True
            u = urlparse(ref)
            if u.scheme in ("http", "https"):
                if n.tag in ("link", "script", "img", "iframe") and n.attrs.get("rel") != "preconnect" \
                        and not ref.startswith(ALLOWED_EXTERNAL):
                    problems.append(f"{name}: external resource {ref}")
                continue
            if u.scheme in ("mailto", "data"):
                continue
            target = name if not u.path else u.path
            if u.path:
                if not (GUIDE / u.path).resolve().exists():
                    problems.append(f"{name}: broken link {ref}")
                    continue
            if u.fragment and target.endswith(".html"):
                if u.fragment not in ids.get(Path(target).name, set()):
                    problems.append(f"{name}: missing anchor {ref}")
    if not has_css:
        problems.append(f"{name}: does not load assets/style.css")
    if not has_js:
        problems.append(f"{name}: does not load assets/site.js")

    # 6: pairing
    for n in nodes:
        lang = n.attrs.get("data-lang")
        if lang not in ("en", "ko") or n.tag == "html":  # <html data-lang> is the switch, set by site.js
            continue
        sibs = n.parent.elements()
        i = sibs.index(n)
        if lang == "en":
            nxt = sibs[i + 1] if i + 1 < len(sibs) else None
            if nxt is None or nxt.attrs.get("data-lang") != "ko" or nxt.tag != n.tag:
                problems.append(f"{name}: <{n.tag} data-lang=en> '{n.text().strip()[:40]}' has no ko twin")
        else:
            prv = sibs[i - 1] if i > 0 else None
            if prv is None or prv.attrs.get("data-lang") != "en" or prv.tag != n.tag:
                problems.append(f"{name}: <{n.tag} data-lang=ko> '{n.text().strip()[:40]}' has no en twin")
        if not n.text().strip() and not n.attrs.get("aria-label"):
            problems.append(f"{name}: empty <{n.tag} data-lang={lang}>")

    # 7: stray text
    def stray(node, inside):
        for c in node.children:
            if isinstance(c, str):
                if not inside and re.search(r"[A-Za-z가-힣]", c):
                    problems.append(f"{name}: untranslated text in <{node.tag}>: '{c.strip()[:50]}'")
            else:
                ok = inside or c.tag in SKIP_TEXT or "data-lang" in c.attrs or c.attrs.get("data-i18n") == "none"
                stray(c, ok)
    body = next((n for n in nodes if n.tag == "body"), None)
    if body is None:
        problems.append(f"{name}: no <body>")
    else:
        stray(body, False)


def collect_ids(name: str) -> set[str]:
    tb = TreeBuilder()
    tb.feed((GUIDE / name).read_text(encoding="utf-8"))
    return {n.attrs["id"] for n in walk(tb.root) if "id" in n.attrs}


def main() -> int:
    problems: list[str] = []
    present = [p for p in PAGES if (GUIDE / p).exists()]
    for p in PAGES:
        if p not in present:
            problems.append(f"missing page {p}")
    ids = {p: collect_ids(p) for p in present}
    for p in present:
        check_page(p, ids, problems)
    for p in sorted(GUIDE.glob("*.html")):
        if p.name not in PAGES:
            problems.append(f"unlisted page {p.name}: add it to PAGES")
    if problems:
        print("\n".join(problems))
        print(f"FAIL: {len(problems)} problem(s) in {len(PAGES)} pages")
        return 1
    print(f"OK: {len(PAGES)} pages, links, anchors and both languages complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
