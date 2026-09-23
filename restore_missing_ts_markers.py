#!/usr/bin/env python3
"""
Restore missing \\ts\\* markers from the catalogs. Does NOT touch \\p / \\m.

  python3 restore_missing_ts_markers.py ult               # every ULT book with missing \\ts\\*
  python3 restore_missing_ts_markers.py ust --dry-run     # report only, write nothing
  python3 restore_missing_ts_markers.py ult zec           # one book (or several: ult zec isa)
  python3 restore_missing_ts_markers.py ult zec --chapters 3 4
  python3 restore_missing_ts_markers.py ult zec --output /tmp/38-ZEC.usfm

Prints a status line per book, then a total. Also rewrites malformed chunk markers that
stand alone on a line (\\ts, \\ts*, and the old \\s5) as \\ts\\*.

Every \\ts\\* that cannot be placed is reported as an ERROR with the reason (the others
are still written); the exit status is 1 if there were any errors.
"""
import argparse
import glob
import json
import os
import re
import sys

from check_markers import check_book, split_key
from restore_common import UsfmDoc, PlaceError, only_insertions, TS_LINE, ROOT, HERE

MALFORMED = re.compile(r"^\\(ts\*?|s5)\s*$")


def restore_book(res, cat, dry_run=False, chapters=None, input_path=None, output=None):
    """Restore one book. Returns a stats dict (and prints nothing)."""
    path = os.path.join(ROOT, f"en_{res}", cat["file"])
    original = open(input_path or path, encoding="utf-8").read()

    # 1. normalize malformed chunk markers
    lines, fixed = original.split("\n"), 0
    for i, l in enumerate(lines):
        if MALFORMED.match(l.strip()):
            lines[i], fixed = TS_LINE, fixed + 1
    text = "\n".join(lines)

    # 2. place missing \ts\*
    missing = check_book(cat, text)["ts_missing"]
    doc = UsfmDoc(text)
    placed, errors, done = [], [], set()
    for key in missing:
        ref, sub = split_key(key)
        ch, v = ref.split(":")
        if chapters and int(ch) not in chapters:
            continue
        if v == "0" and int(ch) not in doc.has_intro:
            v = "1"      # no \\d intro now: a chapter-start \\ts\\* goes before \\c either way
        if not sub and v == "1" and int(ch) not in doc.has_intro:
            if (ch, "c") in done:
                continue
            done.add((ch, "c"))
        try:
            doc.place_ts(int(ch), v, sub, cat["anchors"].get(key))
            placed.append(key)
        except PlaceError as e:
            errors.append(str(e))
    out = doc.render()

    st = {"book": cat["book"], "file": cat["file"], "missing": len(missing), "placed": len(placed),
          "errors": errors, "fixed": fixed, "warnings": [], "written": None}
    if not only_insertions(text, out, {"ts": len(placed)}):
        st["errors"].append("SAFETY CHECK FAILED: output is not the input plus \\ts\\* lines; book NOT written")
        st["placed"] = 0
        return st
    after = check_book(cat, out)["ts_missing"]
    st["warnings"] = [f"{k} was inserted but still does not read back as present" for k in placed if k in after]
    if not dry_run and (placed or fixed or output):
        dest = output or path
        with open(dest, "w", encoding="utf-8") as f:
            f.write(out)
        st["written"] = dest
    return st


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("resource", choices=["ult", "ust"], type=str.lower)
    ap.add_argument("book", nargs="*", help="optional book code(s); default: every book")
    ap.add_argument("--chapters", nargs="*", type=int, help="only these chapters (one book only)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--input", help="read this file instead of the repo file (one book only, for testing)")
    ap.add_argument("--output", help="write here instead of the repo file (one book only)")
    a = ap.parse_args()
    books = [b.lower() for b in a.book]
    if (a.chapters or a.input or a.output) and len(books) != 1:
        ap.error("--chapters / --input / --output need exactly one book")

    files = sorted(glob.glob(os.path.join(HERE, "catalog", f"{a.resource}_*_markers.json")))
    cats = [json.load(open(f, encoding="utf-8")) for f in files]
    cats.sort(key=lambda c: c["file"])
    if books:
        known = {c["book"].lower() for c in cats}
        for b in books:
            if b not in known:
                ap.error(f"no catalog for {a.resource} {b}")
        cats = [c for c in cats if c["book"].lower() in books]

    tot = {"books": 0, "missing": 0, "placed": 0, "errors": 0, "fixed": 0, "warnings": 0}
    for cat in cats:
        st = restore_book(a.resource, cat, a.dry_run, a.chapters, a.input, a.output)
        if not (st["missing"] or st["fixed"] or books):
            continue
        tot["books"] += 1
        for k in ("missing", "placed", "fixed"):
            tot[k] += st[k]
        tot["errors"] += len(st["errors"])
        tot["warnings"] += len(st["warnings"])
        status = "OK" if not st["errors"] else "ERRORS"
        print(f"{a.resource.upper()} {st['book']:4} {st['file']:14} missing {st['missing']:4}  placed {st['placed']:4}  "
              f"not placed {len(st['errors']):2}" + (f"  malformed fixed {st['fixed']}" if st["fixed"] else "")
              + f"  {status}" + ("" if st["written"] or a.dry_run or not st["placed"] else "")
              + ("  (dry run)" if a.dry_run else ""))
        for e in st["errors"]:
            print(f"    ERROR: {e}")
        for w in st["warnings"]:
            print(f"    WARNING: {w}")
    print(f"TOTAL {a.resource.upper()}: {tot['books']} book(s), {tot['missing']} \\ts\\* missing, {tot['placed']} placed, "
          f"{tot['errors']} not placed, {tot['fixed']} malformed fixed, {tot['warnings']} warnings"
          + ("  (dry run: nothing written)" if a.dry_run else ""))
    return 1 if tot["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
