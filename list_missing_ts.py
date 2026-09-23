#!/usr/bin/env python3
"""
List where \\ts\\* markers from the catalog are missing in the current file, by
chapter, with the copy the catalog took them from and the commit that last
removed \\ts\\* from that chapter (\\s5, the old chunk marker, counts as \\ts\\*).

Usage: python3 list_missing_ts.py ult 1ti [--text]
"""
from collections import OrderedDict

from _missing_common import load, verse_text, ROOT
from survey_history import ts_per_chapter
from usfm_markers import GitBlobReader, file_history
import os


def removal_commits(repo, path):
    """{chapter: (newer_commit_row, older_count, newer_count)} for the most recent
    commit (master history) that lowered the chapter's \\ts\\* count."""
    reader = GitBlobReader(repo)
    rows = []
    for sha, date, author, subj in file_history(repo, path):
        blob = reader.read(sha, path)
        if blob is not None:
            rows.append(((sha, date, author, subj), ts_per_chapter(blob)))
    reader.close()
    out = {}
    for (newer, n_counts), (_, o_counts) in zip(rows, rows[1:]):
        for c in set(n_counts) | set(o_counts):
            if c not in out and n_counts.get(c, 0) < o_counts.get(c, 0):
                out[c] = (newer, o_counts.get(c, 0), n_counts.get(c, 0))
    return out


def fmt(row):
    sha, date, author, subj = row
    return f"{sha[:9]} {date[:10]} {author} \"{subj[:70]}\""


cat, r, texts = load(__doc__)
missing = r["ts_missing"]
print(f"{cat['resource'].upper()} {cat['book']}: {len(missing)} of {r['ts_catalog']} \\ts\\* missing")
if not missing:
    raise SystemExit
by_ch = OrderedDict()
for key in missing:
    by_ch.setdefault(int(key.split(":")[0]), []).append(key)

repo = os.path.join(ROOT, f"en_{cat['resource']}")
removed = removal_commits(repo, cat["file"])
commits = cat["baseline_commits"]
ov = cat.get("ts_override") or {}
for c, keys in by_ch.items():
    src = ov.get("ts_commit") if ov.get("ts_commit") and (not ov.get("ts_chapters") or c in ov["ts_chapters"]) \
        else cat["chapter_baselines"].get(str(c))
    info = commits.get(src[:9] if src and src != "current" else "current", {})
    print(f"\nChapter {c}: {len(keys)} missing")
    print(f"  catalog copy : {src} {info.get('date', '')[:10]} {info.get('author', '')} \"{info.get('subject', '')[:60]}\"")
    if c in removed:
        row, before, after = removed[c]
        print(f"  last removed : {fmt(row)}  ({before} -> {after})")
    for k in keys:
        print(f"    {k}" + (f"  {verse_text(texts, k)}" if texts else ""))
