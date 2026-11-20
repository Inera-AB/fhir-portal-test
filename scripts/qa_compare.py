#!/usr/bin/env python3
"""Compare the QA results of two IG Publisher builds of the same IG.

Reads qa.json (counts) and qa-eslintcompact.txt (one line per message) from
each build's output folder. Messages are compared without their line and
column numbers and with the build's own folder stripped from paths, so the
same message in both builds matches. Prints a Markdown report: the counts
side by side, then the messages only in the new build and only in the old.

Usage:
  qa_compare.py --old base/output --new cand/output
                [--old-label 3.0.4] [--new-label 3.0.5] [--report report.md]
"""

import argparse
import json
import os
import re
import sys
from collections import Counter

COUNTS = [("errs", "Fel"), ("warnings", "Varningar"), ("hints", "Tips"), ("broken-links", "Trasiga länkar")]
LINE = re.compile(r"^(?P<loc>.*?): line \d+, col \d+, (?P<level>\w+) - (?P<msg>.*)$")
BUILD_PATH = re.compile(r"^.*?/(output|fsh-generated/resources|temp/pages)/")


def find(output, name):
    """qa files sit at the output root, or in a language folder."""
    for candidate in [os.path.join(output, name)] + [
        os.path.join(output, d, name) for d in sorted(os.listdir(output))
        if os.path.isdir(os.path.join(output, d))
    ]:
        if os.path.exists(candidate):
            return candidate
    return None


def counts(output):
    path = find(output, "qa.json")
    if not path:
        return None
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return {key: data.get(key) for key, _ in COUNTS} | {"tool": data.get("tool")}


def normalise_location(loc):
    loc = BUILD_PATH.sub("", loc.strip())
    return loc.split("#")[0]


def messages(output):
    path = find(output, "qa-eslintcompact.txt")
    found = Counter()
    if not path:
        return found
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = LINE.match(line.rstrip("\n"))
            if m:
                found[(m["level"], normalise_location(m["loc"]), m["msg"].strip())] += 1
    return found


def report(old_label, new_label, old_counts, new_counts, old_msgs, new_msgs, limit=100):
    out = [f"## QA: {old_label} jämfört med {new_label}", ""]
    out += [f"| | {old_label} | {new_label} | Skillnad |", "|---|---|---|---|"]
    for key, label in COUNTS:
        a = (old_counts or {}).get(key)
        b = (new_counts or {}).get(key)
        diff = (b - a) if isinstance(a, int) and isinstance(b, int) else ""
        sign = f"+{diff}" if isinstance(diff, int) and diff > 0 else str(diff)
        mark = " ⚠️" if key in ("errs", "broken-links") and isinstance(diff, int) and diff > 0 else ""
        out.append(f"| {label} | {a if a is not None else '–'} | {b if b is not None else '–'} | {sign}{mark} |")
    out.append("")

    new_only = new_msgs - old_msgs
    old_only = old_msgs - new_msgs

    def listing(title, items):
        out.append(f"### {title} ({sum(items.values())})")
        out.append("")
        if not items:
            out.append("Inga.")
            out.append("")
            return
        order = {"Fatal": 0, "Error": 1, "Warning": 2, "Information": 3}
        rows = sorted(items.items(), key=lambda kv: (order.get(kv[0][0], 9), kv[0][1]))
        out.append("| Nivå | Var | Meddelande |")
        out.append("|---|---|---|")
        for (level, loc, msg), n in rows[:limit]:
            times = f" (×{n})" if n > 1 else ""
            text = msg.replace("|", "\\|")
            if len(text) > 300:
                text = text[:300] + "…"
            out.append(f"| {level} | `{loc}` | {text}{times} |")
        if len(rows) > limit:
            out.append(f"| | | … och {len(rows) - limit} till |")
        out.append("")

    listing(f"Nya meddelanden med {new_label}", new_only)
    listing(f"Meddelanden som försvann med {new_label}", old_only)
    return "\n".join(out)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--old", required=True)
    p.add_argument("--new", required=True)
    p.add_argument("--old-label", default="tidigare")
    p.add_argument("--new-label", default="ny")
    p.add_argument("--report")
    a = p.parse_args(argv)
    text = report(a.old_label, a.new_label, counts(a.old), counts(a.new), messages(a.old), messages(a.new))
    if a.report:
        with open(a.report, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
