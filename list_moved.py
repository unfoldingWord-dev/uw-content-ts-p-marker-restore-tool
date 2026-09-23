#!/usr/bin/env python3
"""
List \\p / \\m markers from the catalog that are missing at their spot in the
current file but look moved or changed: the verse before/after now has a \\p/\\m
("moved to"), or there is a \\q# / other paragraph marker in or around the verse
("changed").

Usage: python3 list_moved.py ust zec [--text]
"""
from _missing_common import load, verse_text

cat, r, texts = load(__doc__)
items = r["pm_missing_explained"]
print(f"{cat['resource'].upper()} {cat['book']}: {len(items)} \\p/\\m missing here but moved or changed nearby")
for e in items:
    moved = [w.split(" at ")[1] for w in e["why"] if w.startswith("p/m at ")]
    other = [w for w in e["why"] if not w.startswith("p/m at ")]
    parts = []
    if moved:
        parts.append("moved to " + ", ".join(moved))
    if other:
        parts.append("; ".join(other))
    line = f"{e['ref']:>9}  \\{e['marker']}  ->  {' | '.join(parts)}"
    if texts:
        line += "\n           " + verse_text(texts, e["ref"])
    print(line)
