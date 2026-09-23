#!/usr/bin/env python3
"""
Restore missing \\p / \\m markers from the catalogs, chapter by chapter, by severity.
Does NOT touch \\ts\\*.

Only "unexplained" losses are ever restored: a catalog \\p/\\m that is missing now with
no new \\p/\\m in the verse before/after and no \\q# / other paragraph marker in or
around the verse (see check_markers.py). Each CHAPTER gets a severity, so a chapter
whose editor dropped paragraphs can be restored while a neighbouring chapter whose
editor moved them on purpose is left alone:

  large     3+ unexplained in the chapter AND nothing moved/changed in the chapter
  moderate  2+ unexplained (and not large)
  few       exactly 1 unexplained

  python3 restore_missing_pm_markers.py ust --list                      # every UST book: chapters + severity
  python3 restore_missing_pm_markers.py ust --severity large            # restore large chapters in every book
  python3 restore_missing_pm_markers.py ust zec --list                  # one book (or several: ust zec isa)
  python3 restore_missing_pm_markers.py ust zec --severity large moderate --dry-run
  python3 restore_missing_pm_markers.py ust zec --severity few --chapters 3 14

Prints a status line per book, then a total. Any \\p/\\m that cannot be placed is
reported as an ERROR (the others are still written); exit status 1 if there were any.
"""
import argparse
import glob
import json
import os
import sys
from collections import defaultdict

from check_markers import check_book, split_key
from restore_common import UsfmDoc, PlaceError, only_insertions, ROOT, HERE

LEVELS = ("large", "moderate", "few")


def chapter_severity(result):
    """{chapter: (severity, [unexplained entries], [explained entries])}"""
    un, ex = defaultdict(list), defaultdict(list)
    for e in result["pm_missing_unexplained"]:
        un[int(e["ref"].split(":")[0])].append(e)
    for e in result["pm_missing_explained"]:
        ex[int(e["ref"].split(":")[0])].append(e)
    out = {}
    for c in sorted(set(un) | set(ex)):
        u, x = len(un[c]), len(ex[c])
        sev = ("large" if u >= 3 and x == 0 else "moderate" if u >= 2 else "few" if u == 1 else None)
        out[c] = (sev, un[c], ex[c])
    return out


def list_book(res, cat, text):
    """Print the chapter table for one book; return {severity: count of unexplained}."""
    sev = chapter_severity(check_book(cat, text))
    counts = defaultdict(int)
    for s, un, _ in sev.values():
        if s:
            counts[s] += len(un)
    if not sev:
        return counts
    print(f"{res.upper()} {cat['book']} ({cat['file']}): chapters with missing \\p/\\m")
    print(f"  {'ch':>3}  {'severity':9} {'unexplained':>11} {'moved/changed':>13}  unexplained refs")
    for c, (s, un, ex) in sev.items():
        print(f"  {c:>3}  {s or '-':9} {len(un):>11} {len(ex):>13}  "
              + " ".join(f"{e['ref']}\\{e['marker']}" for e in un))
    print("  book totals: " + ", ".join(f"{k} {counts[k]}" for k in LEVELS))
    return counts


def restore_book(cat, text, severities, chapters=None):
    """Place the \\p/\\m for the chosen severities. Returns (output text or None, stats)."""
    sev = chapter_severity(check_book(cat, text))
    doc = UsfmDoc(text)
    placed, errors = defaultdict(int), []
    todo = [(c, e) for c, (s, un, _) in sev.items() if s in severities
            and (not chapters or c in chapters) for e in un]
    for c, e in todo:
        ref, sub = split_key(e["ref"])
        ch, v = ref.split(":")
        try:
            doc.place_para(e["marker"], int(ch), v, sub, cat["anchors"].get(e["ref"]))
            placed[e["marker"]] += 1
        except PlaceError as err:
            errors.append((e["ref"], str(err)))
    out = doc.render()
    st = {"todo": len(todo), "p": placed["p"], "m": placed["m"], "errors": [m for _, m in errors],
          "warnings": []}
    if not only_insertions(text, out, dict(placed)):
        st["errors"].append("SAFETY CHECK FAILED: output is not the input plus \\p/\\m lines; book NOT written")
        st["p"] = st["m"] = 0
        return None, st
    still = {e["ref"] for e in check_book(cat, out)["pm_missing_unexplained"]}
    failed = {r for r, _ in errors}
    st["warnings"] = [f"{e['ref']} \\{e['marker']} was inserted but still reads as missing"
                      for _, e in todo if e["ref"] in still and e["ref"] not in failed]
    return out, st


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("resource", choices=["ult", "ust"], type=str.lower)
    ap.add_argument("book", nargs="*", help="optional book code(s); default: every book")
    ap.add_argument("--severity", nargs="+", choices=LEVELS)
    ap.add_argument("--list", action="store_true", help="only list chapters with their severity")
    ap.add_argument("--chapters", nargs="*", type=int, help="only these chapters (one book only)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--input", help="read this file instead of the repo file (one book only, for testing)")
    ap.add_argument("--output", help="write here instead of the repo file (one book only)")
    a = ap.parse_args()
    if not a.list and not a.severity:
        ap.error("give --severity (large / moderate / few) or --list")
    books = [b.lower() for b in a.book]
    if (a.chapters or a.input or a.output) and len(books) != 1:
        ap.error("--chapters / --input / --output need exactly one book")

    cats = [json.load(open(f, encoding="utf-8"))
            for f in glob.glob(os.path.join(HERE, "catalog", f"{a.resource}_*_markers.json"))]
    cats.sort(key=lambda c: c["file"])
    if books:
        known = {c["book"].lower() for c in cats}
        for b in books:
            if b not in known:
                ap.error(f"no catalog for {a.resource} {b}")
        cats = [c for c in cats if c["book"].lower() in books]

    if a.list:
        tot = defaultdict(int)
        for cat in cats:
            path = os.path.join(ROOT, f"en_{a.resource}", cat["file"])
            for k, n in list_book(a.resource, cat, open(a.input or path, encoding="utf-8").read()).items():
                tot[k] += n
        print(f"TOTAL {a.resource.upper()} unexplained \\p/\\m: " + ", ".join(f"{k} {tot[k]}" for k in LEVELS))
        return 0

    tot = {"books": 0, "todo": 0, "p": 0, "m": 0, "errors": 0, "warnings": 0}
    for cat in cats:
        path = os.path.join(ROOT, f"en_{a.resource}", cat["file"])
        text = open(a.input or path, encoding="utf-8").read()
        out, st = restore_book(cat, text, a.severity, a.chapters)
        if not (st["todo"] or books):
            continue
        written = ""
        if out is not None and not a.dry_run and (st["p"] or st["m"] or a.output):
            dest = a.output or path
            with open(dest, "w", encoding="utf-8") as f:
                f.write(out)
            if a.output:
                written = f"  wrote {dest}"
        tot["books"] += 1
        for k in ("todo", "p", "m"):
            tot[k] += st[k]
        tot["errors"] += len(st["errors"])
        tot["warnings"] += len(st["warnings"])
        print(f"{a.resource.upper()} {cat['book']:4} {cat['file']:14} to restore {st['todo']:4}  "
              f"placed {st['p'] + st['m']:4} (\\p {st['p']}, \\m {st['m']})  not placed {len(st['errors']):2}  "
              f"{'OK' if not st['errors'] else 'ERRORS'}" + ("  (dry run)" if a.dry_run else "") + written)
        for e in st["errors"]:
            print(f"    ERROR: {e}")
        for w in st["warnings"]:
            print(f"    WARNING: {w}")
    print(f"TOTAL {a.resource.upper()} ({'+'.join(a.severity)}): {tot['books']} book(s), {tot['todo']} to restore, "
          f"{tot['p'] + tot['m']} placed (\\p {tot['p']}, \\m {tot['m']}), {tot['errors']} not placed, "
          f"{tot['warnings']} warnings" + ("  (dry run: nothing written)" if a.dry_run else ""))
    return 1 if tot["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
