#!/usr/bin/env python3
"""Compare two builds of an IG page by page, like tracked changes.

For every HTML page in both builds, the page's content area
(#segment-content) is diffed with lxml's htmldiff, and the new page is
written out with the differences marked: inserted text green and
underlined, removed text red and struck through. Pages only in one build
are listed as added or removed. An index.html lists every changed page.

The diff pages keep the new build's own layout. A <base> element points at
where the new build's pages are served, so its styles, images and links
load from there; a banner on each page links back to the index.

Usage:
  htmldiff_site.py --old DIR --new DIR --out DIR --new-base-url URL
                   --report-url URL [--old-label L] [--new-label L]
                   [--title T] [--summary summary.md]
"""

import argparse
import html
import json
import os
import re
import sys

from lxml import etree
from lxml import html as lhtml
from lxml.html.diff import htmldiff

# Renderings of a resource in another format, and pages that only exist for
# testing or history, change with every resource and add only noise.
SKIP = re.compile(
    r"(\.(json|xml|ttl)\.html|-testing\.html|\.change\.history\.html|^qa[^/]*\.html"
    r"|(^|/)qa[^/]*\.html|-(mappings|definitions|examples)\.html)$"
)

STYLE = """
ins.tk-diff, ins { background: #d7f5dd; color: #0b5d1e; text-decoration: underline; }
del.tk-diff, del { background: #fde0e0; color: #8a1010; text-decoration: line-through; }
#tk-diff-banner { position: sticky; top: 0; z-index: 10000; background: #353636; color: #fff;
  font: 14px/1.4 'Open Sans', Arial, sans-serif; padding: 8px 16px; }
#tk-diff-banner a { color: #ffd4e8; }
#tk-diff-banner ins, #tk-diff-banner del { padding: 0 4px; }
"""


def html_pages(root):
    pages = set()
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            if f.endswith(".html"):
                rel = os.path.relpath(os.path.join(dirpath, f), root).replace(os.sep, "/")
                if not SKIP.search(rel):
                    pages.add(rel)
    return pages


def load(path):
    """IG pages are UTF-8; decode first so a page without a charset meta is not read as Latin-1."""
    with open(path, "rb") as f:
        data = f.read()
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return lhtml.document_fromstring(data)
    return lhtml.document_fromstring(data, parser=lhtml.HTMLParser(encoding="utf-8"))


def content_element(doc):
    found = doc.xpath('//*[@id="segment-content"]')
    if found:
        return found[0]
    body = doc.find("body")
    return body if body is not None else doc


def remove_keeping_tail(node):
    parent = node.getparent()
    if parent is None:
        return
    if node.tail:
        prev = node.getprevious()
        if prev is not None:
            prev.tail = (prev.tail or "") + node.tail
        else:
            parent.text = (parent.text or "") + node.tail
    parent.remove(node)


def drop_volatile(el):
    """Parts that differ between any two builds (the release banner), and
    nodes htmldiff cannot carry (comments, scripts)."""
    for node in el.xpath('.//*[@id="publish-box"] | .//script | .//comment() | .//processing-instruction()'):
        remove_keeping_tail(node)
    return el


def inner_html(el):
    parts = [el.text or ""] if el.text else []
    for child in el:
        parts.append(etree.tostring(child, encoding="unicode", method="html"))
    return "".join(parts)


def normalised(text):
    return re.sub(r"\s+", " ", text).strip()


def diff_page(old_path, new_path):
    """Return (diff_html_fragment, inserted, deleted) or None if unchanged."""
    old_el = drop_volatile(content_element(load(old_path)))
    new_doc = load(new_path)
    new_el = drop_volatile(content_element(new_doc))
    old_html, new_html = inner_html(old_el), inner_html(new_el)
    if normalised(old_html) == normalised(new_html):
        return None
    diffed = htmldiff(old_html, new_html)
    inserted = len(re.findall(r"<ins>", diffed))
    deleted = len(re.findall(r"<del>", diffed))
    if inserted == 0 and deleted == 0:
        return None
    return new_doc, new_el, diffed, inserted, deleted


def write_diff_page(out_path, new_doc, new_el, diffed, base_url, banner):
    for child in list(new_el):
        new_el.remove(child)
    new_el.text = None
    for node in lhtml.fragments_fromstring(diffed):
        if isinstance(node, str):
            if len(new_el):
                new_el[-1].tail = (new_el[-1].tail or "") + node
            else:
                new_el.text = (new_el.text or "") + node
        else:
            new_el.append(node)

    head = new_doc.find("head")
    if head is None:
        head = etree.SubElement(new_doc, "head")
    for old_base in head.findall("base"):
        head.remove(old_base)
    base = etree.Element("base", href=base_url)
    head.insert(0, base)
    style = etree.SubElement(head, "style")
    style.text = STYLE

    body = new_doc.find("body")
    if body is not None:
        body.insert(0, lhtml.fragment_fromstring(banner))

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(lhtml.tostring(new_doc, doctype="<!DOCTYPE html>", encoding="utf-8"))


def page_title(path):
    try:
        t = load(path).findtext(".//title")
        return normalised(t) if t else os.path.basename(path)
    except Exception:  # an unreadable page still gets listed
        return os.path.basename(path)


def index_html(title, old_label, new_label, changed, added, removed, unchanged, new_base_url):
    def rows(items, link):
        return "\n".join(
            f"<tr><td><a href=\"{html.escape(link(p))}\">{html.escape(t)}</a></td>"
            f"<td><code>{html.escape(p)}</code></td><td>{extra}</td></tr>"
            for p, t, extra in items
        )

    changed_rows = rows(
        [(p, t, f"<ins>+{i}</ins> <del>−{d}</del>") for p, t, i, d in changed],
        lambda p: "diff/" + p,
    )
    added_rows = rows([(p, t, "") for p, t in added], lambda p: new_base_url + p)
    removed_rows = "\n".join(
        f"<tr><td>{html.escape(t)}</td><td><code>{html.escape(p)}</code></td><td></td></tr>"
        for p, t in removed
    )

    def section(heading, body, empty):
        if not body:
            return f"<h2>{heading}</h2><p class=\"empty\">{empty}</p>"
        return (f"<h2>{heading}</h2><table><thead><tr><th>Sida</th><th>Fil</th><th></th></tr></thead>"
                f"<tbody>{body}</tbody></table>")

    return f"""<!DOCTYPE html>
<html lang="sv"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
body {{ font-family: 'Open Sans', Arial, sans-serif; color: #353636; max-width: 1100px; margin: 2rem auto; padding: 0 1rem; }}
h1 {{ color: #7E2A4C; }} h2 {{ color: #7E2A4C; margin-top: 2rem; }}
table {{ border-collapse: collapse; width: 100%; }} th, td {{ text-align: left; padding: .4rem .6rem; border-bottom: 1px solid #ddd; }}
ins {{ background: #d7f5dd; color: #0b5d1e; text-decoration: none; padding: 0 .3rem; }}
del {{ background: #fde0e0; color: #8a1010; text-decoration: none; padding: 0 .3rem; }}
.empty {{ color: #727373; }}
</style></head><body>
<h1>{html.escape(title)}</h1>
<p>Från <strong>{html.escape(old_label)}</strong> till <strong>{html.escape(new_label)}</strong>.
{len(changed)} ändrade, {len(added)} nya och {len(removed)} borttagna sidor; {unchanged} oförändrade.
Ändrade sidor visas i den nya versionens layout, med tillagd text <ins>markerad</ins> och borttagen text <del>överstruken</del>.</p>
{section("Ändrade sidor", changed_rows, "Inga sidor har ändrats.")}
{section("Nya sidor", added_rows, "Inga nya sidor.")}
{section("Borttagna sidor", removed_rows, "Inga sidor har tagits bort.")}
</body></html>
"""


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--old", required=True)
    p.add_argument("--new", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--new-base-url", required=True, help="URL the new build's pages are served from, ending in /")
    p.add_argument("--report-url", required=True, help="URL the report (--out) will be served from, ending in /")
    p.add_argument("--old-label", default="tidigare")
    p.add_argument("--new-label", default="ny")
    p.add_argument("--title", default="Jämförelse")
    p.add_argument("--summary", help="also write a Markdown summary here")
    a = p.parse_args(argv)

    new_base = a.new_base_url if a.new_base_url.endswith("/") else a.new_base_url + "/"
    report_url = a.report_url if a.report_url.endswith("/") else a.report_url + "/"
    old_pages, new_pages = html_pages(a.old), html_pages(a.new)

    changed, unchanged = [], 0
    for rel in sorted(old_pages & new_pages):
        result = diff_page(os.path.join(a.old, rel), os.path.join(a.new, rel))
        if result is None:
            unchanged += 1
            continue
        new_doc, new_el, diffed, inserted, deleted = result
        title = normalised(new_doc.findtext(".//title") or rel)
        page_dir = rel.rsplit("/", 1)[0] + "/" if "/" in rel else ""
        banner = (
            f'<div id="tk-diff-banner">Ändringar från <strong>{html.escape(a.old_label)}</strong> till '
            f'<strong>{html.escape(a.new_label)}</strong>: <ins>+{inserted}</ins> <del>−{deleted}</del> · '
            f'<a href="{html.escape(report_url)}index.html">Alla ändrade sidor</a> · '
            f'<a href="{html.escape(new_base + rel)}">Sidan utan markeringar</a></div>'
        )
        write_diff_page(os.path.join(a.out, "diff", rel), new_doc, new_el, diffed, new_base + page_dir, banner)
        changed.append((rel, title, inserted, deleted))

    added = [(rel, page_title(os.path.join(a.new, rel))) for rel in sorted(new_pages - old_pages)]
    removed = [(rel, page_title(os.path.join(a.old, rel))) for rel in sorted(old_pages - new_pages)]

    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "index.html"), "w", encoding="utf-8") as f:
        f.write(index_html(a.title, a.old_label, a.new_label, changed, added, removed, unchanged, new_base))
    summary = {
        "old": a.old_label, "new": a.new_label, "unchanged": unchanged,
        "changed": [{"page": p, "title": t, "inserted": i, "deleted": d} for p, t, i, d in changed],
        "added": [p for p, _ in added], "removed": [p for p, _ in removed],
    }
    with open(os.path.join(a.out, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    if a.summary:
        lines = [
            f"## {a.title}", "",
            f"Från **{a.old_label}** till **{a.new_label}**: {len(changed)} ändrade, {len(added)} nya och "
            f"{len(removed)} borttagna sidor; {unchanged} oförändrade.", "",
            f"**[Visa ändringarna]({report_url}index.html)**", "",
        ]
        if changed:
            lines += ["| Sida | Tillagt | Borttaget |", "|---|---|---|"]
            for rel, title, i, d in sorted(changed, key=lambda c: -(c[2] + c[3]))[:30]:
                lines.append(f"| [{title}]({report_url}diff/{rel}) | {i} | {d} |")
            if len(changed) > 30:
                lines.append(f"| … och {len(changed) - 30} sidor till | | |")
        with open(a.summary, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    print(f"{len(changed)} changed, {len(added)} added, {len(removed)} removed, {unchanged} unchanged")
    return 0


if __name__ == "__main__":
    sys.exit(main())
