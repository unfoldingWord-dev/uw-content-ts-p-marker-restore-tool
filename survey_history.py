#!/usr/bin/env python3
"""
Survey \\ts\\* / \\p / \\m counts across the master (first-parent) history of every
book, pick a baseline commit, and flag books whose current file looks damaged.

Source is chosen PER CHAPTER: a chapter with at least ceil(verses / 6) \\ts\\* (min 1)
today is taken from the current file; a chapter with fewer is taken from the newest commit
(without duplicated chapters/verses) where it still had its \\ts\\* (see below).
The book-level "baseline" (newest commit with >= THRESH * max for the whole book)
is kept for reference only.

Writes survey/<res>_survey.json and prints a table.

Usage: survey_history.py [--repo en_ult|en_ust ...] [--book ZEC ...]
"""
import argparse
import glob
import hashlib
import json
import math
import os
import re
import subprocess
import sys

from usfm_markers import GitBlobReader, file_history

HERE = os.path.dirname(os.path.abspath(__file__))
# folder that holds the en_ult / en_ust checkouts (default: the parent of this tool)
ROOT = os.path.abspath(os.environ.get("UW_CONTENT_DIR") or os.path.dirname(HERE))
THRESH = 0.95
MAX_CHUNK = 6   # a \\ts\\* chunk is usually 2-6 verses, so a chapter of V verses needs
                # at least ceil(V / 6) \\ts\\* (and at least 1); fewer means damaged
_VN = re.compile(r"\\c\s+(\d+)|\\v\s+(\d+)(?:-(\d+))?")


def verses_per_chapter(text):
    out, ch = {}, 0
    for m in _VN.finditer(text):
        if m.group(1):
            ch = int(m.group(1))
            out.setdefault(ch, 0)
        else:
            out[ch] = max(out.get(ch, 0), int(m.group(3) or m.group(2)))
    return out


def min_ts(verses):
    return max(1, math.ceil(verses / MAX_CHUNK))


PM_RE = re.compile(r"^\\[pm][ \t]*$", re.M)
C_RE = re.compile(r"^\\c\s+(\d+)", re.M)
_CH_TOK = re.compile(r"\\c\s+(\d+)|\\ts\\?\*|\\ts\b|\\s5\b|\\v\s+\d")   # \s5 = old chunk marker


def ts_count(text):
    return sum(ts_per_chapter(text).values())


def ts_per_chapter(text):
    """{chapter: number of verses preceded by a \\ts\\*}. Token based (markers
    need not start a line); stacked duplicate \\ts\\* count once; a \\ts\\*
    before \\c N counts for chapter N."""
    counts, ch, waiting = {}, 0, False
    for m in _CH_TOK.finditer(text):
        if m.group(1):
            ch = int(m.group(1))
            counts.setdefault(ch, 0)
        elif m.group(0).startswith(("\\ts", "\\s5")):
            waiting = True
        elif waiting:
            counts[ch] = counts.get(ch, 0) + 1
            waiting = False
    return counts


# ---------------------------------------------------------------------------
# Bible Editor (BE) / bot content. Text a BE or BW Bot commit wrote into a chapter
# is never used as a source, even when it reached master through someone else's
# merge commit. A later human edit of that chapter makes new text, which is fine.
# ---------------------------------------------------------------------------
BE_AUTHORS = {"BW Bot"}
BE_SUBJECT_MARK = "bible-editor"   # "bible-editor export: ...", "Merge pull request 'bible-editor: X ult → master'"
_C_SPLIT = re.compile(r"\\c\s+(\d+)")


def is_be_commit(author, subject):
    return author in BE_AUTHORS or BE_SUBJECT_MARK in subject.lower()


def chapter_hashes(text):
    """{chapter: sha1 of that chapter's raw USFM (from its \\c to the next \\c)}"""
    out, ms = {}, list(_C_SPLIT.finditer(text))
    for i, m in enumerate(ms):
        end = ms[i + 1].start() if i + 1 < len(ms) else len(text)
        out[int(m.group(1))] = hashlib.sha1(text[m.start():end].encode()).hexdigest()
    return out


def be_tainted_chapters(repo, path, reader):
    """Set of (chapter, hash) that some BE/bot commit (on any branch) changed."""
    out = subprocess.run(["git", "-C", repo, "log", "--all", "--format=%H%x1f%P%x1f%an%x1f%s", "--", path],
                         capture_output=True, text=True, check=True).stdout
    tainted = set()
    for line in out.splitlines():
        sha, parents, author, subj = line.split("\x1f")
        if not is_be_commit(author, subj):
            continue
        blob = reader.read(sha, path)
        if blob is None:
            continue
        mine = chapter_hashes(blob)
        before = {}
        for par in parents.split()[:1]:
            pb = reader.read(par, path)
            if pb:
                before = chapter_hashes(pb)
        for c, h in mine.items():
            if before.get(c) != h:
                tainted.add((c, h))
    return tainted


V_RE = re.compile(r"\\(c|v)\s+(\d+)")


def has_duplicate_verses(text):
    seen, ch = set(), 0
    for kind, n in V_RE.findall(text):
        if kind == "c":
            ch = int(n)
            key = ("c", ch)
        else:
            key = (ch, int(n))
        if key in seen:
            return True
        seen.add(key)
    return False


def survey_book(reader, repo, path):
    hist = file_history(repo, path)
    rows = []
    for sha, date, author, subj in hist:
        blob = reader.read(sha, path)
        if blob is None:
            continue
        rows.append({"sha": sha, "date": date, "author": author, "subject": subj,
                     "ts": ts_count(blob), "pm": len(PM_RE.findall(blob)),
                     "dup": has_duplicate_verses(blob), "ch": ts_per_chapter(blob),
                     "hash": chapter_hashes(blob), "be": is_be_commit(author, subj)})
    if not rows:
        return None
    max_ts = max(r["ts"] for r in rows)
    base = next((r for r in rows if r["ts"] >= THRESH * max_ts and not r["dup"]), None) \
        or next((r for r in rows if r["ts"] >= THRESH * max_ts), rows[-1])
    cur_blob = open(os.path.join(repo, path), encoding="utf-8").read()
    per_ch = ts_per_chapter(cur_blob)
    tainted = be_tainted_chapters(repo, path, reader)
    # per-chapter sources. The verse count only decides whether to CHECK history:
    # a chapter with fewer than ceil(verses / 6) \\ts\\* (min 1) today is checked. If
    # some older (non-BE, non-duplicated) copy had MORE \\ts\\* than today, take the
    # newest such copy and keep walking back while the count still rises (BE sometimes
    # removed them a few per commit), using the newest commit at that peak. If no copy
    # ever had more, the chapter is fine as it is ("checked_ok").
    nverses = verses_per_chapter(cur_blob)
    need = {c: min_ts(nverses.get(c, 0)) for c in per_ch}
    chapter_baselines = {}
    for c in sorted(per_ch):
        n = per_ch[c]
        if n >= need[c]:
            chapter_baselines[c] = {"sha": "current", "date": None, "ts": n}
            continue
        pick, best, started = None, n, False
        skipped_be = 0
        for r in rows:
            if r["dup"]:
                continue
            if r["be"] or (c, r["hash"].get(c)) in tainted:
                skipped_be += 1
                continue
            k = r["ch"].get(c, 0)
            if not started:
                if k > n:
                    started, pick, best = True, r, k
                continue
            if k > best:
                pick, best = r, k
            elif k < best:
                break
        if pick is None:
            chapter_baselines[c] = {"sha": "current", "date": None, "ts": n, "checked_ok": True,
                                    "verses": nverses.get(c, 0), "needed": need[c]}
        else:
            chapter_baselines[c] = {"sha": pick["sha"], "date": pick["date"], "ts": best,
                                    "verses": nverses.get(c, 0), "needed": need[c], "ts_now": n,
                                    "author": pick["author"], "subject": pick["subject"]}
    damaged = sorted(c for c, b in chapter_baselines.items() if b["sha"] != "current")
    # first commit (oldest) after baseline where ts dropped
    idx = rows.index(base)
    first_drop = rows[idx - 1] if idx > 0 and rows[idx - 1]["ts"] < base["ts"] else None
    return {
        "file": path,
        "current_ts": ts_count(cur_blob),
        "current_pm": len(PM_RE.findall(cur_blob)),
        "max_ts": max_ts,
        "baseline": {k: base[k] for k in ("sha", "date", "author", "subject", "ts", "pm", "dup")},
        "first_drop_after_baseline": first_drop and {k: first_drop[k] for k in ("sha", "date", "author", "subject", "ts")},
        "chapters": len(C_RE.findall(cur_blob)),
        "damaged_chapters": damaged,
        "current_ts_per_chapter": per_ch,
        "chapter_baselines": chapter_baselines,
        "commits_scanned": len(rows),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", nargs="*", default=["en_ult", "en_ust"])
    ap.add_argument("--book", nargs="*")
    a = ap.parse_args()
    os.makedirs(os.path.join(HERE, "survey"), exist_ok=True)
    for rname in a.repo:
        repo = os.path.join(ROOT, rname)
        reader = GitBlobReader(repo)
        res = rname.split("_")[1]
        out_path = os.path.join(HERE, "survey", f"{res}_survey.json")
        results = {}
        if a.book and os.path.exists(out_path):
            results = json.load(open(out_path))
        files = sorted(os.path.basename(f) for f in glob.glob(os.path.join(repo, "[0-9][0-9]-*.usfm")))
        for f in files:
            bk = f[3:6]
            if a.book and bk not in a.book:
                continue
            r = survey_book(reader, repo, f)
            if r is None:
                continue
            results[bk] = r
            b = r["baseline"]
            flag = f"{len(r['damaged_chapters'])} ch <2 ts" if r["damaged_chapters"] else "ok"
            print(f"{res} {bk} cur_ts={r['current_ts']:4d} base_ts={b['ts']:4d} "
                  f"cur_pm={r['current_pm']:4d} base_pm={b['pm']:4d} base={b['sha'][:9]} {b['date'][:10]} "
                  f"{flag} of {r['chapters']}", flush=True)
        reader.close()
        json.dump(results, open(out_path, "w"), indent=1, ensure_ascii=False)


if __name__ == "__main__":
    sys.exit(main())
