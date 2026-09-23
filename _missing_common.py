"""Shared by list_unexplained.py and list_moved.py: check one book against its catalog."""
import argparse
import json
import os
import sys

from check_markers import check_book
from usfm_markers import parse_markers

HERE = os.path.dirname(os.path.abspath(__file__))
# folder that holds the en_ult / en_ust checkouts (default: the parent of this tool)
ROOT = os.path.abspath(os.environ.get("UW_CONTENT_DIR") or os.path.dirname(HERE))


def load(description):
    ap = argparse.ArgumentParser(description=description)
    ap.add_argument("resource", help="ult or ust")
    ap.add_argument("book", help="book code, e.g. zec")
    ap.add_argument("--text", action="store_true", help="also show the start of each verse")
    a = ap.parse_args()
    res, bk = a.resource.lower(), a.book.lower()
    path = os.path.join(HERE, "catalog", f"{res}_{bk}_markers.json")
    if not os.path.exists(path):
        sys.exit(f"No catalog {os.path.relpath(path)} (run build_catalog.py)")
    cat = json.load(open(path, encoding="utf-8"))
    usfm = open(os.path.join(ROOT, f"en_{res}", cat["file"]), encoding="utf-8").read()
    texts = {v.ref: v.text for v in parse_markers(usfm)} if a.text else {}
    return cat, check_book(cat, usfm), texts


def verse_text(texts, key, width=70):
    t = texts.get(key.rstrip("abcdefghijklmnopqrstuvwxyz"), "")
    return (t[:width] + "…") if len(t) > width else t
