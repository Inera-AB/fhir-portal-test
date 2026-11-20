"""Tests for the scripts behind the test workflows.

Run with:  python3 -m unittest discover -s scripts -v
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))

import cross_ig_links  # noqa: E402
import qa_compare  # noqa: E402


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


class CrossIgLinksTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        # A web root with the template published as 2.3.0.
        self.web = os.path.join(root, "web")
        write(os.path.join(self.web, "ig/index.html"), "catalog")
        write(os.path.join(self.web, "ig/template/2.3.0/index.html"), "x")
        write(os.path.join(self.web, "ig/template/2.3.0/sv/profiles.html"), "x")
        write(os.path.join(self.web, "ig/template/package-list.json"),
              json.dumps({"list": [{"version": "current"}, {"version": "2.3.0"}]}))
        for cmd in (["init", "-q"], ["add", "."],
                    ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "web"]):
            subprocess.run(["git", "-C", self.web, *cmd], check=True)

        # A build of an IG at https://fhir.inera.se/ig/demo, version 1.0.0.
        self.out = os.path.join(root, "output")
        write(os.path.join(self.out, "ImplementationGuide-inera.test.demo.json"), json.dumps({
            "resourceType": "ImplementationGuide",
            "url": "https://fhir.inera.se/ig/demo/ImplementationGuide/inera.test.demo",
            "packageId": "inera.test.demo",
            "version": "1.0.0",
            "dependsOn": [
                {"packageId": "inera.core.template", "version": "2.3.0", "uri": "https://fhir.inera.se/ig/template"},
                {"packageId": "inera.core.other", "version": "9.9.9", "uri": "https://fhir.inera.se/ig/other"},
                {"packageId": "hl7.fhir.uv.extensions.r5", "version": "5.4.0", "uri": "http://hl7.org/fhir/extensions"},
            ],
        }))
        write(os.path.join(self.out, "sv/StructureDefinition-demo.html"), "x")
        links = [
            "https://fhir.inera.se/ig/template/2.3.0/sv/profiles.html#top",   # ok
            "https://fhir.inera.se/ig/template/9.9.9/index.html",             # broken
            "http://fhir.inera.se/ig/demo/1.0.0/sv/StructureDefinition-demo.html",  # own, ok
            "https://fhir.inera.se/ig/demo/1.0.0/sv/missing.html",            # own, broken
            "https://fhir.inera.se/ig/demo/StructureDefinition/demo",         # own root, canonical form
            "https://fhir.inera.se/ig/demo/package.tgz",                      # made at publication
            "https://fhir.inera.se/ig/",                                      # catalog
            "https://hl7.org/fhir/R5/",                                       # not the site
        ]
        write(os.path.join(self.out, "sv/index.html"),
              "".join(f'<a href="{u}">l</a>' for u in links))

    def tearDown(self):
        self.tmp.cleanup()

    def run_check(self, mode):
        ig = cross_ig_links.load_ig(self.out, "inera.test.demo")
        web = cross_ig_links.WebRoot(self.web)
        return ig, web, cross_ig_links.check_links(self.out, web, "https://fhir.inera.se/ig/", ig, mode)

    def test_links_milestone(self):
        _ig, _web, links = self.run_check("milestone")
        self.assertEqual(sorted(links["broken"]), [
            "https://fhir.inera.se/ig/demo/1.0.0/sv/missing.html",
            "https://fhir.inera.se/ig/template/9.9.9/index.html",
        ])
        self.assertIn("https://fhir.inera.se/ig/template/2.3.0/sv/profiles.html", links["ok"])
        self.assertIn("https://fhir.inera.se/ig/demo/1.0.0/sv/StructureDefinition-demo.html", links["ok"])
        self.assertIn("https://fhir.inera.se/ig/demo/StructureDefinition/demo", links["ok"])
        self.assertIn("https://fhir.inera.se/ig/demo/package.tgz", links["ok"])
        self.assertIn("https://fhir.inera.se/ig/", links["ok"])
        self.assertNotIn("https://hl7.org/fhir/R5/", {**links["ok"], **links["broken"]})
        self.assertEqual(links["root_only"], {})

    def test_links_working_mode_flags_root_links(self):
        _ig, _web, links = self.run_check("working")
        self.assertIn("https://fhir.inera.se/ig/demo/StructureDefinition/demo", links["root_only"])

    def test_dependencies(self):
        ig, web, _links = self.run_check("milestone")
        deps = {pid: ok for pid, _v, _n, ok, _why in cross_ig_links.check_dependencies(web, "https://fhir.inera.se/ig/", ig)}
        self.assertEqual(deps, {"inera.core.template": True, "inera.core.other": False})


class QaCompareTest(unittest.TestCase):
    def test_messages_match_across_builds(self):
        with tempfile.TemporaryDirectory() as root:
            for build, extra in (("a", ""), ("b", "/x/b/output/sv/p.html: line 3, col 1, Error - New problem\n")):
                write(os.path.join(root, build, "qa.json"), json.dumps({"errs": len(extra) and 1, "warnings": 1}))
                write(os.path.join(root, build, "qa-eslintcompact.txt"),
                      f"/x/{build}/output/sv/p.html: line {7 if build == 'a' else 9}, col 2, Warning - Same\n" + extra)
            old = qa_compare.messages(os.path.join(root, "a"))
            new = qa_compare.messages(os.path.join(root, "b"))
            self.assertEqual(list(new - old), [("Error", "sv/p.html", "New problem")])
            self.assertEqual(list(old - new), [])
            text = qa_compare.report("a", "b", qa_compare.counts(os.path.join(root, "a")),
                                     qa_compare.counts(os.path.join(root, "b")), old, new)
            self.assertIn("| Fel | 0 | 1 | +1 ⚠️ |", text)


class HtmlDiffTest(unittest.TestCase):
    def test_changed_page_is_marked_and_comments_survive(self):
        try:
            import htmldiff_site
        except ImportError:
            self.skipTest("lxml not installed")
        page = ('<html><head><title>T</title></head><body><div id="segment-content">'
                '<!-- c --><p id="publish-box">v{v}</p><p>{body}</p></div></body></html>')
        with tempfile.TemporaryDirectory() as root:
            write(os.path.join(root, "old/sv/a.html"), page.format(v="1", body="hej och hå"))
            write(os.path.join(root, "new/sv/a.html"), page.format(v="2", body="hej och tjo"))
            write(os.path.join(root, "old/sv/same.html"), page.format(v="1", body="lika"))
            write(os.path.join(root, "new/sv/same.html"), page.format(v="2", body="lika"))
            write(os.path.join(root, "new/sv/a.json.html"), "skipped")
            out = os.path.join(root, "report")
            htmldiff_site.main(["--old", os.path.join(root, "old"), "--new", os.path.join(root, "new"),
                                "--out", out, "--new-base-url", "https://x/", "--report-url", "https://r/"])
            summary = json.load(open(os.path.join(out, "summary.json"), encoding="utf-8"))
            self.assertEqual([c["page"] for c in summary["changed"]], ["sv/a.html"])
            self.assertEqual(summary["unchanged"], 1)
            self.assertEqual(summary["added"], [])
            diff = open(os.path.join(out, "diff/sv/a.html"), encoding="utf-8").read()
            self.assertIn("<ins>tjo</ins>", diff)
            self.assertIn("<del>hå</del>", diff)
            self.assertIn('<base href="https://x/sv/">', diff)
            self.assertNotIn("cyfunction", diff)


if __name__ == "__main__":
    unittest.main()
