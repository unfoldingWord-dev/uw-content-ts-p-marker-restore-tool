#!/usr/bin/env python3
"""
List \\p / \\m markers from the catalog that are missing in the current file with
nothing replacing them (no \\p/\\m in the verse before/after, no \\q# or other
paragraph marker in that verse or the verses around it).

Usage: python3 list_unexplained.py ust zec [--text]
"""
from _missing_common import load, verse_text

cat, r, texts = load(__doc__)
items = r["pm_missing_unexplained"]
print(f"{cat['resource'].upper()} {cat['book']}: {len(items)} \\p/\\m missing with nothing replacing them")
for e in items:
    line = f"{e['ref']:>9}  \\{e['marker']}"
    if texts:
        line += "  " + verse_text(texts, e["ref"])
    print(line)
