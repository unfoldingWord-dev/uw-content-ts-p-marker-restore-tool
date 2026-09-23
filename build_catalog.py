#!/usr/bin/env python3
"""
Build <res>_<book>_markers.json catalogs of where \\ts\\*, \\p and \\m were in a
known-good (baseline) version of a book.

Sources come from survey/<res>_survey.json (run survey_history.py first): a
chapter with 2+ \\ts\\* today is taken from the current file ("current"); a chapter
with fewer is taken from its own older commit ("chapter_baselines"). --commit
forces every chapter to come from one commit.

Reference keys:
  "1:0"   marker(s) come before a \\d chapter description (intro / "verse 0")
  "1:1"   marker(s) come before verse 1:1 (a \\ts\\* before \\c 1 is recorded here,
          unless a \\d comes between them: then it is 1:0)
  "1:2b"  marker(s) come mid-verse, before the 2nd sentence of 1:2
  "1:2c"  ... before the 3rd sentence, etc.
Each key maps to the markers there, in document order, e.g. ["ts", "p"].
A "sentence" boundary is . ? ! : ; , a closing quote after punctuation, or a comma
before an opening quote (see usfm_markers._SENT_END). Markers that split a clause
anywhere else are not cataloged (treated as mistakes); they are listed in "skipped".
A \\ts\\* at a \\q# poetry line inside a sentence is kept (anchors: poetry_line), and
so is a break after a short verse-initial greeting like "Dear Theophilus," (greeting).
Every mid-verse anchor records "after_punct", the punctuation the break follows. Poetry \\q# markers are never cataloged.
Mid-verse entries also get an "anchors" entry with the words on either side so a
restore can still find the spot if the verse's wording changed since the baseline.

Usage:
  build_catalog.py                      # every book in both repos
  build_catalog.py --repo en_ult --book ZEC
  build_catalog.py --repo en_ult --book ZEC --commit cfdb22d5d
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys

from usfm_markers import (GitBlobReader, parse_markers, sentence_index,
                          split_sentences, letter, POETRY_RE, HEADINGS)

HERE = os.path.dirname(os.path.abspath(__file__))
# folder that holds the en_ult / en_ust checkouts (default: the parent of this tool)
ROOT = os.path.abspath(os.environ.get("UW_CONTENT_DIR") or os.path.dirname(HERE))
CATALOG_DIR = os.path.join(HERE, "catalog")
KEEP = ("ts", "p", "m")
ANCHOR_WORDS = 6
GREETING_WORDS = 4
_TRAIL = re.compile(r"[^\w\s]*$")


def trailing_punct(text):
    """Punctuation the break follows, e.g. '.', ':', ',', '?”', '.}' ('' if none)."""
    return _TRAIL.search(text.rstrip()).group(0)


def words_before(text, off, n=ANCHOR_WORDS):
    return " ".join(text[:off].split()[-n:])


def words_after(text, off, n=ANCHOR_WORDS):
    return " ".join(text[off:].split()[:n])


def catalog_from_verses(verses):
    markers, anchors, poetry_context = {}, {}, {}
    counts = {k: 0 for k in KEEP}
    warnings, skipped = [], []
    seen = set()
    for v in verses:
        if v.ref in seen:
            warnings.append(f"{v.ref}: verse appears more than once in baseline (baseline may be corrupt)")
        seen.add(v.ref)
    for v in verses:
        sents = split_sentences(v.text)
        starts = [s for s, _ in sents]
        last = None   # (offset, marker) of last kept marker, reset by a heading
        for off, mk in v.events:
            if mk in HEADINGS:
                last = None
                continue
            if mk not in KEEP:
                continue
            if last == (off, mk):
                warnings.append(f"{v.ref}: duplicate \\{mk} ignored")
                continue
            last = (off, mk)
            counts[mk] += 1
            if off == 0:
                key = v.ref
            else:
                idx = sentence_index(v.text, off)
                key = f"{v.ref}{letter(idx)}"
                poetry_line = mk == "ts" and any(o == off and POETRY_RE.match(k) for o, k in v.events)
                lead = v.text[:off].strip()
                greeting = idx == 0 and lead.endswith(",") and len(lead.split()) <= GREETING_WORDS
                if greeting:
                    # a verse-initial salutation, e.g. "Dear Theophilus, | In the first book"
                    key = f"{v.ref}{letter(1)}"
                    anchors.setdefault(key, {})["greeting"] = True
                elif (idx == 0 or off not in starts) and poetry_line:
                    # a chunk break at a poetry line inside a sentence: keep it
                    key = f"{v.ref}{letter(idx)}"
                    anchors.setdefault(key, {})["poetry_line"] = True
                elif idx == 0 or off not in starts:
                    # splits a clause (e.g. "Hezekiah | from the king"): don't catalog
                    counts[mk] -= 1
                    skipped.append({"ref": v.ref, "marker": mk,
                                    "before": words_before(v.text, off),
                                    "after": words_after(v.text, off)})
                    continue
                a = anchors.setdefault(key, {})
                a["after_punct"] = trailing_punct(v.text[:off])
                a["before"] = words_before(v.text, off)
                a["after"] = words_after(v.text, off)
            markers.setdefault(key, []).append(mk)
        # which verses start inside poetry in the baseline (context for the checker)
        if v.events and any(POETRY_RE.match(mk) for off, mk in v.events if off == 0):
            poetry_context[v.ref] = True
    return markers, anchors, counts, warnings, skipped, sorted(poetry_context, key=_refkey)


def _refkey(r):
    c, v = r.split(":")
    return (int(c), int(v.split("-")[0].rstrip("abcdefghijklmnopqrstuvwxyz")))


def load_overrides():
    p = os.path.join(HERE, "catalog_overrides.json")
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}


def _keysort(k):
    c, v = k.split(":")
    m = re.match(r"(\d+)(?:-\d+)?([a-z]*)", v)
    return (int(c), int(m.group(1)), m.group(2))


def apply_ts_override(ov, markers, anchors, counts, skipped, repo, path, reader):
    """Replace \\ts\\* entries with those from ov["ts_commit"] (optionally only in
    ov["ts_chapters"]); \\p/\\m entries are kept."""
    sha = commit_info(repo, ov["ts_commit"])["sha"]
    chapters = set(ov.get("ts_chapters") or [])
    in_scope = lambda k: not chapters or int(k.split(":")[0]) in chapters
    o_markers, o_anchors, o_counts, _, o_skipped, _ = catalog_from_verses(parse_markers(reader.read(sha, path)))
    new = {}
    for k, v in markers.items():
        keep = [m for m in v if m != "ts" or not in_scope(k)]
        if keep:
            new[k] = keep
    for k, v in o_markers.items():
        if in_scope(k) and "ts" in v:
            new[k] = ["ts"] * v.count("ts") + new.get(k, [])
            if k in o_anchors:
                anchors[k] = dict(o_anchors[k], ts_from=sha[:9])
    anchors = {k: a for k, a in anchors.items() if k in new}
    counts = dict(counts, ts=sum(v.count("ts") for v in new.values()))
    skipped = [x for x in skipped if x["marker"] != "ts" or not in_scope(x["ref"])] + \
              [x for x in o_skipped if x["marker"] == "ts" and in_scope(x["ref"])]
    return dict(sorted(new.items(), key=lambda kv: _keysort(kv[0]))), anchors, counts, skipped


def commit_info(repo, sha):
    out = subprocess.run(["git", "-C", repo, "show", "-s", "--format=%H%x1f%aI%x1f%an%x1f%s", sha],
                         capture_output=True, text=True, check=True).stdout.strip()
    h, d, a, s = out.split("\x1f")
    return {"sha": h, "date": d, "author": a, "subject": s}


def build(res, bk, survey, repo, reader, commit=None):
    path = survey["file"]
    if commit:
        sha = commit_info(repo, commit)["sha"]
        ch_sha = {int(c): sha for c in survey["chapter_baselines"]}
    else:
        ch_sha = {int(c): b["sha"] for c, b in survey["chapter_baselines"].items()}
    head = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"], capture_output=True,
                          text=True, check=True).stdout.strip()
    checked_ok = [int(c) for c, b in survey["chapter_baselines"].items() if b.get("checked_ok")]
    parsed = {}
    verses = []
    for c in sorted(ch_sha):
        sha = ch_sha[c]
        if sha not in parsed:
            if sha == "current":
                usfm = open(os.path.join(repo, path), encoding="utf-8").read()
            else:
                usfm = reader.read(sha, path)
            if usfm is None:
                raise SystemExit(f"{path} not found at {sha}")
            parsed[sha] = parse_markers(usfm)
        verses += [v for v in parsed[sha] if v.chapter == c]
    markers, anchors, counts, warnings, skipped, poetry = catalog_from_verses(verses)
    override = load_overrides().get(res, {}).get(bk, {})
    if override.get("ts_commit"):  # noqa
        markers, anchors, counts, skipped = apply_ts_override(
            override, markers, anchors, counts, skipped, repo, path, reader)
        parsed.setdefault(commit_info(repo, override["ts_commit"])["sha"], None)
    commits = {sha: (commit_info(repo, sha) if sha != "current" else
                     dict(commit_info(repo, head), subject="current working file (HEAD " + head[:9] + ")"))
               for sha in parsed}
    cat = {
        "resource": res,
        "book": bk,
        "file": path,
        "generated": datetime.date.today().isoformat(),
        "baseline_commits": {("current" if sha == "current" else sha[:9]): {"date": i["date"], "author": i["author"], "subject": i["subject"],
                                       "chapters": [c for c in sorted(ch_sha) if ch_sha[c] == sha]}
                             for sha, i in commits.items()},
        "chapter_baselines": {str(c): ch_sha[c][:9] for c in sorted(ch_sha)},
        "chapters_restored_from_history": sorted(c for c in ch_sha if ch_sha[c] != "current"),
        "chapters_checked_ok": sorted(checked_ok),
        "ts_override": override or None,
        "counts": counts,
        "markers": markers,
        "anchors": anchors,
        "baseline_poetry_verses": poetry,
    }
    if skipped:
        cat["skipped"] = skipped
    if warnings:
        cat["warnings"] = warnings
    os.makedirs(CATALOG_DIR, exist_ok=True)
    out = os.path.join(CATALOG_DIR, f"{res}_{bk.lower()}_markers.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(cat, f, indent=1, ensure_ascii=False)
        f.write("\n")
    mid = sum(1 for k in markers if k[-1].isalpha())
    oldest = min(i["date"] for i in commits.values())[:10]
    hist = sum(1 for c in ch_sha if ch_sha[c] != "current")
    print(f"{res} {bk}: {hist}/{len(ch_sha)} ch from history ({len(commits)} source(s), oldest {oldest})  "
          f"ts={counts['ts']} p={counts['p']} m={counts['m']} refs={len(markers)} mid-verse={mid} "
          f"skipped={len(skipped)} warnings={len(warnings)} -> {os.path.relpath(out, ROOT)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", nargs="*", default=["en_ult", "en_ust"])
    ap.add_argument("--book", nargs="*")
    ap.add_argument("--commit", help="baseline commit (only with a single --book)")
    a = ap.parse_args()
    for rname in a.repo:
        res = rname.split("_")[1]
        repo = os.path.join(ROOT, rname)
        survey = json.load(open(os.path.join(HERE, "survey", f"{res}_survey.json")))
        reader = GitBlobReader(repo)
        for bk, s in survey.items():
            if a.book and bk not in a.book:
                continue
            build(res, bk, s, repo, reader, a.commit)
        reader.close()


if __name__ == "__main__":
    sys.exit(main())
