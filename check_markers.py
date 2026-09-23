#!/usr/bin/env python3
"""
Compare each catalog (catalog/<res>_<book>_markers.json) with the current USFM file
and report what is missing.

  \\ts\\*      missing = catalog has a \\ts\\* at a spot where the current file has none.
  \\p / \\m    missing = catalog has one where the current file has none:
      "explained"   -> the verse before/after now starts with a \\p or \\m that the
                       catalog did not have there (moved), or a
                       \\q# / other paragraph marker is in that verse or the verse
                       before/after (changed to poetry / another paragraph style)
      "unexplained" -> none of the above: probably lost (or the editor forgot it)

Mid-verse spots (e.g. "3:4b") count as present if the current verse has that marker
anywhere inside it (the sentence letter may shift when wording changes).

Writes report/missing_report.json (every ref) and report/missing_report.md (summary).

Usage: check_markers.py [--repo en_ult en_ust] [--book ZEC ...]
"""
import argparse
import glob
import json
import os
import re
import sys

from usfm_markers import parse_markers, POETRY_RE, OTHER_PARA

HERE = os.path.dirname(os.path.abspath(__file__))
# folder that holds the en_ult / en_ust checkouts (default: the parent of this tool)
ROOT = os.path.abspath(os.environ.get("UW_CONTENT_DIR") or os.path.dirname(HERE))
PARA_LIKE = {"p", "m"} | OTHER_PARA - {"b"}


def split_key(key):
    m = re.match(r"^(\d+):(\d+(?:-\d+)?)([a-z]?)$", key)
    return m.group(1) + ":" + m.group(2), m.group(3)


def current_index(usfm):
    """ordered refs, {ref: set(markers at verse start)}, {ref: set(markers mid-verse)}"""
    order, start, mid = [], {}, {}
    for v in parse_markers(usfm):
        if v.ref in start:          # duplicate verse: merge
            pass
        else:
            order.append(v.ref)
            start[v.ref], mid[v.ref] = set(), set()
        for off, mk in v.events:
            (start if off == 0 else mid)[v.ref].add(mk)
    return order, start, mid


def resolve_ref(ref, known, intro_chapters=None):
    """Map a catalog ref onto the current file's refs: exact match; a catalog bridge
    "4-5" -> current "4"; a catalog "5" -> a current bridge "4-5"; "N:0" -> "N:1"
    when the current chapter has no \\d intro."""
    if ref in known:
        return ref
    ch, v = ref.split(":")
    if v == "0":
        return f"{ch}:1" if f"{ch}:1" in known else ref
    first = v.split("-")[0]
    if f"{ch}:{first}" in known:
        return f"{ch}:{first}"
    n = int(first)
    for k in known:
        c, vv = k.split(":")
        if c == ch and "-" in vv:
            a, b = vv.split("-")
            if int(a) <= n <= int(b):
                return k
    return ref


def check_book(cat, usfm):
    order, start, mid = current_index(usfm)
    pos = {r: i for i, r in enumerate(order)}
    known = set(order)

    def neighbours(ref):
        i = pos.get(ref)
        if i is None:
            return []
        return [order[j] for j in (i - 1, i + 1) if 0 <= j < len(order)]

    ts_missing, pm_unexplained, pm_explained = [], [], []
    ts_total = pm_total = 0
    seen = set()   # (current ref, sub, marker) already counted
    for key, mks in cat["markers"].items():
        ref, sub = split_key(key)
        ref = resolve_ref(ref, known)
        # Several catalog verses can land on one current verse: catalog 8 and 9 -> a
        # current bridge 8-9 (the marker goes before the bridge), or N:0 and N:1 ->
        # N:1 when there is no \d intro now. Count each marker there once.
        mks = [m for m in dict.fromkeys(mks) if sub or (ref, sub, m) not in seen]
        seen.update((ref, sub, m) for m in mks)
        here = (mid if sub else start).get(ref, set())
        allv = start.get(ref, set()) | mid.get(ref, set())
        for mk in mks:
            if mk == "ts":
                ts_total += 1
                if "ts" not in here:
                    ts_missing.append(key)
                continue
            pm_total += 1
            if mk in here:
                continue
            reasons = []
            if sub and (allv & {"p", "m"}):
                reasons.append("p/m elsewhere in verse")
            if any(POETRY_RE.match(x) for x in allv):
                reasons.append("q# in verse")
            if (start.get(ref, set()) & PARA_LIKE) - {mk}:
                reasons.append("other paragraph marker: " + ",".join(sorted((start[ref] & PARA_LIKE) - {mk})))
            for n in neighbours(ref):
                # only a NEW \p/\m next door counts as "moved" (not one the catalog already had)
                if start.get(n, set()) & {"p", "m"} and not set(cat["markers"].get(n, [])) & {"p", "m"}:
                    reasons.append(f"p/m at {n}")
                if any(POETRY_RE.match(x) for x in start.get(n, set()) | mid.get(n, set())):
                    reasons.append(f"q# at {n}")
            if ref not in pos:
                reasons.append("verse not found in current file")
            entry = {"ref": key, "marker": mk}
            if reasons:
                entry["why"] = sorted(set(reasons))
                pm_explained.append(entry)
            else:
                pm_unexplained.append(entry)
    return {
        "ts_catalog": ts_total, "ts_missing": ts_missing,
        "pm_catalog": pm_total, "pm_missing_unexplained": pm_unexplained,
        "pm_missing_explained": pm_explained,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", nargs="*", default=["en_ult", "en_ust"])
    ap.add_argument("--book", nargs="*")
    a = ap.parse_args()
    results = []
    for rname in a.repo:
        res = rname.split("_")[1]
        for f in sorted(glob.glob(os.path.join(HERE, "catalog", f"{res}_*_markers.json"))):
            cat = json.load(open(f, encoding="utf-8"))
            if a.book and cat["book"] not in a.book:
                continue
            usfm = open(os.path.join(ROOT, rname, cat["file"]), encoding="utf-8").read()
            r = check_book(cat, usfm)
            r.update(resource=res, book=cat["book"], file=cat["file"],
                     chapters_from_history=cat["chapters_restored_from_history"])
            results.append(r)
    os.makedirs(os.path.join(HERE, "report"), exist_ok=True)
    json.dump(results, open(os.path.join(HERE, "report", "missing_report.json"), "w"),
              indent=1, ensure_ascii=False)

    lines = ["# Missing \\ts\\* / \\p / \\m report", "",
             "| Res | Book | \\ts\\* missing | of catalog | \\p/\\m missing, unexplained | \\p/\\m missing, explained | Chapters from history |",
             "|---|---|---:|---:|---:|---:|---:|"]
    rows = [r for r in results if r["ts_missing"] or r["pm_missing_unexplained"] or r["pm_missing_explained"]]
    rows.sort(key=lambda r: (-len(r["ts_missing"]), -len(r["pm_missing_unexplained"])))
    for r in rows:
        lines.append(f"| {r['resource']} | {r['book']} | {len(r['ts_missing'])} | {r['ts_catalog']} | "
                     f"{len(r['pm_missing_unexplained'])} | {len(r['pm_missing_explained'])} | "
                     f"{len(r['chapters_from_history'])} |")
    open(os.path.join(HERE, "report", "missing_report.md"), "w").write("\n".join(lines) + "\n")
    for r in rows:
        print(f"{r['resource']} {r['book']:4} ts_missing={len(r['ts_missing']):4}/{r['ts_catalog']:<5} "
              f"pm_unexplained={len(r['pm_missing_unexplained']):4} pm_explained={len(r['pm_missing_explained']):4}")
    print(f"{len(rows)} of {len(results)} books have something missing")


if __name__ == "__main__":
    sys.exit(main())
