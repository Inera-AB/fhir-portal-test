#!/usr/bin/env python3
"""Check that links from a built IG to the shared web root will resolve once
the IG is published.

An IG links to fhir.inera.se in three ways: to its own published pages (its
canonical, with or without the version folder), to other Inera IGs (by hand
in the narrative, or generated for a dependency), and to the shared catalog.
IG Publisher's own link check cannot follow any of them: the target is the
web root, not the build. This script resolves each such link against the
build output (own pages) or a checkout of the web root (everything else),
and checks that every Inera dependency is published in the version the IG
asks for.

The web root is read with git (`ls-tree` and `show`), so a clone made with
`--filter=blob:none --no-checkout` is enough: no published files are
downloaded.

Usage:
  cross_ig_links.py --output ig/output --web-git web [--site-base URL]
                    [--publication-request ig/publication-request.json]
                    [--report report.md] [--fail]
"""

import argparse
import glob
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from html.parser import HTMLParser
from urllib.parse import urldefrag, urlsplit

DEFAULT_SITE_BASE = "https://fhir.inera.se/ig/"

# Files the publication run creates in the IG's folder, so they cannot be in
# a branch build yet.
CREATED_AT_PUBLICATION = {
    "package.tgz", "package-list.json", "history.html", "directory.html",
    "package.manifest.json", "full-ig.zip", "examples.json.zip",
    "definitions.json.zip", "publication-request.json",
}

# The Type/id form of a canonical URL: the publication run writes a redirect
# for it, resolving to the resource's page.
RESOURCE_URL = re.compile(r"^([A-Z][A-Za-z]+)/([A-Za-z0-9\-.]{1,64})$")


class _Links(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hrefs = []

    def handle_starttag(self, tag, attrs):
        if tag in ("a", "link", "area"):
            for name, value in attrs:
                if name == "href" and value:
                    self.hrefs.append(value)


def page_links(path):
    parser = _Links()
    with open(path, encoding="utf-8", errors="replace") as f:
        parser.feed(f.read())
    return parser.hrefs


def normalise(url):
    """Drop the fragment and query, and use https for the site."""
    url = urldefrag(url)[0]
    parts = urlsplit(url)
    url = parts._replace(query="").geturl()
    if url.startswith("http://"):
        url = "https://" + url[len("http://"):]
    return url


def load_ig(output, package_id=None):
    """The built ImplementationGuide resource: package id, version, canonical, deps."""
    pattern = f"ImplementationGuide-{package_id}.json" if package_id else "ImplementationGuide-*.json"
    for path in sorted(glob.glob(os.path.join(output, pattern))):
        with open(path, encoding="utf-8") as f:
            ig = json.load(f)
        if ig.get("resourceType") == "ImplementationGuide":
            url = ig.get("url", "")
            canonical = url.split("/ImplementationGuide/")[0] if "/ImplementationGuide/" in url else url
            return {
                "package_id": ig.get("packageId", ""),
                "version": ig.get("version", ""),
                "canonical": canonical.rstrip("/"),
                "depends_on": ig.get("dependsOn", []),
            }
    raise SystemExit(f"No {pattern} in {output}")


class WebRoot:
    """Paths in the web root's git checkout, read without downloading blobs."""

    def __init__(self, git_dir, ref="HEAD"):
        self.git_dir = git_dir
        self.ref = ref
        self._trees = {}

    def _git(self, *args):
        return subprocess.run(["git", "-C", self.git_dir, *args], check=True,
                              capture_output=True, text=True).stdout

    def tree(self, top):
        """All file paths under ig/<top>, as a set (cached per IG folder)."""
        if top not in self._trees:
            try:
                out = self._git("ls-tree", "-r", "--name-only", self.ref, "--", f"ig/{top}")
            except subprocess.CalledProcessError:
                out = ""
            self._trees[top] = set(out.splitlines())
        return self._trees[top]

    def exists(self, rel):
        """rel is a path below ig/, e.g. 'template/2.3.0/index.html'."""
        top = rel.split("/", 1)[0]
        paths = self.tree(top)
        target = "ig/" + rel.rstrip("/")
        if target in paths:
            return True
        # A folder: the server answers with its index.html.
        prefix = target + "/"
        return any(p.startswith(prefix) for p in paths)

    def show(self, rel):
        try:
            return self._git("show", f"{self.ref}:ig/{rel}")
        except subprocess.CalledProcessError:
            return None


def own_target_exists(output, rel):
    """Whether rel, a path below the IG's own canonical, exists once published."""
    rel = rel.strip("/")
    if rel == "":
        rel = "index.html"
    if rel in CREATED_AT_PUBLICATION:
        return True
    candidates = [rel, rel + "/index.html", rel + ".html"]
    m = RESOURCE_URL.match(rel)
    if m:
        candidates.append(f"{m.group(1)}-{m.group(2)}.html")
    langs = [d for d in os.listdir(output) if os.path.isdir(os.path.join(output, d)) and len(d) in (2, 5)]
    for c in candidates:
        for prefix in [""] + [lang + "/" for lang in langs]:
            if os.path.exists(os.path.join(output, prefix + c)):
                return True
    return False


def check_links(output, web, site_base, ig, mode):
    """Return {url: [source pages]} per outcome: ok, broken, root_only."""
    own_name = ig["canonical"][len(site_base):].split("/")[0] if ig["canonical"].startswith(site_base.rstrip("/")) else None
    version = ig["version"]
    sources = defaultdict(set)
    for path in glob.glob(os.path.join(output, "**", "*.html"), recursive=True):
        rel_page = os.path.relpath(path, output)
        for href in page_links(path):
            url = normalise(href)
            if url.startswith(site_base) or url + "/" == site_base:
                sources[url].add(rel_page)

    result = {"ok": {}, "broken": {}, "root_only": {}}
    for url, pages in sorted(sources.items()):
        rel = url[len(site_base):] if url.startswith(site_base) else ""
        name, _, rest = rel.partition("/")
        if own_name and name == own_name:
            if rest == version or rest.startswith(version + "/"):
                ok = own_target_exists(output, rest[len(version):])
                bucket = "ok" if ok else "broken"
            else:
                ok = own_target_exists(output, rest)
                # The IG's root is only replaced by a milestone publication.
                bucket = ("ok" if mode == "milestone" else "root_only") if ok else "broken"
        elif rel == "":
            bucket = "ok" if web.exists("index.html") else "broken"
        else:
            bucket = "ok" if web.exists(rel) else "broken"
        result[bucket][url] = sorted(pages)
    return result


def check_dependencies(web, site_base, ig):
    """Inera dependencies: is the requested version published in the web root?"""
    rows = []
    for dep in ig["depends_on"]:
        pid, ver, uri = dep.get("packageId", ""), dep.get("version", ""), dep.get("uri", "")
        if not pid.startswith("inera."):
            continue
        name = pid.split(".")[2] if pid.count(".") >= 2 else pid
        if uri.startswith(site_base):
            name = uri[len(site_base):].split("/")[0] or name
        listing = web.show(f"{name}/package-list.json")
        if listing is None:
            rows.append((pid, ver, name, False, "ingen package-list.json i webbroten (aldrig publicerad)"))
            continue
        try:
            versions = [e.get("version") for e in json.loads(listing).get("list", [])]
        except json.JSONDecodeError:
            rows.append((pid, ver, name, False, "package-list.json går inte att läsa"))
            continue
        if ver in ("current", "dev") or not ver:
            rows.append((pid, ver, name, False, "beroendet är inte låst till en publicerad version"))
        elif ver in versions:
            rows.append((pid, ver, name, True, "publicerad"))
        else:
            published = ", ".join(v for v in versions if v and v != "current") or "inga"
            rows.append((pid, ver, name, False, f"versionen finns inte (publicerade: {published})"))
    return rows


def report(ig, mode, links, deps, max_sources=3):
    out = [f"## Länkar mellan IG:er: `{ig['package_id']}#{ig['version']}`", ""]
    out.append(f"Canonical `{ig['canonical']}`, publiceringsläge `{mode}`.")
    out.append("")
    out.append("| Utfall | Antal länkar |")
    out.append("|---|---|")
    out.append(f"| ✅ Löses upp vid publicering | {len(links['ok'])} |")
    out.append(f"| ⚠️ Löses bara upp efter en milestone-release | {len(links['root_only'])} |")
    out.append(f"| ❌ Löses inte upp | {len(links['broken'])} |")
    out.append("")

    def table(title, items):
        if not items:
            return
        out.append(f"### {title}")
        out.append("")
        out.append("| Länk | Används på |")
        out.append("|---|---|")
        for url, pages in items.items():
            shown = ", ".join(f"`{p}`" for p in pages[:max_sources])
            more = f" och {len(pages) - max_sources} till" if len(pages) > max_sources else ""
            out.append(f"| {url} | {shown}{more} |")
        out.append("")

    table("Länkar som inte löses upp", links["broken"])
    table("Länkar till IG:ns rot (kräver `mode: milestone`)", links["root_only"])

    out.append("### Inera-beroenden")
    out.append("")
    if deps:
        out.append("| Paket | Version | Katalog | Status |")
        out.append("|---|---|---|---|")
        for pid, ver, name, ok, why in deps:
            out.append(f"| `{pid}` | `{ver}` | `ig/{name}/` | {'✅' if ok else '❌'} {why} |")
    else:
        out.append("IG:n har inga beroenden till andra Inera-IG:er.")
    out.append("")
    return "\n".join(out)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--output", required=True, help="the IG's build output folder")
    p.add_argument("--web-git", required=True, help="git checkout of the web root repo (gh-pages)")
    p.add_argument("--web-ref", default="HEAD")
    p.add_argument("--package-id", help="the IG's package id (id in sushi-config.yaml)")
    p.add_argument("--site-base", default=DEFAULT_SITE_BASE)
    p.add_argument("--publication-request", help="publication-request.json, for mode")
    p.add_argument("--report", help="write the Markdown report here (default: stdout)")
    p.add_argument("--fail", action="store_true", help="exit 1 when a link or dependency is broken")
    a = p.parse_args(argv)

    site_base = a.site_base if a.site_base.endswith("/") else a.site_base + "/"
    ig = load_ig(a.output, a.package_id)
    mode = "milestone"
    if a.publication_request and os.path.exists(a.publication_request):
        with open(a.publication_request, encoding="utf-8") as f:
            mode = json.load(f).get("mode", "milestone")
    web = WebRoot(a.web_git, a.web_ref)
    links = check_links(a.output, web, site_base, ig, mode)
    deps = check_dependencies(web, site_base, ig)
    text = report(ig, mode, links, deps)
    if a.report:
        with open(a.report, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    else:
        print(text)
    broken = bool(links["broken"]) or any(not ok for *_, ok, _why in deps)
    return 1 if (broken and a.fail) else 0


if __name__ == "__main__":
    sys.exit(main())
