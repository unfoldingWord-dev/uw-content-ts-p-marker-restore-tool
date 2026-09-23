"""
Placement engine shared by restore_missing_ts_markers.py and restore_missing_pm_markers.py.

Works on the raw (aligned) USFM as a list of lines and only ever INSERTS lines
(\\ts\\*, \\p, \\m, and a blank line above them where the house style needs one).
The one exception: when a paragraph break lands right after an opening quote that
sits at the end of the previous line (…\\zaln-e\\*, “), the quote is moved down
to the start of the new paragraph's first line.

Formatting rules (same as replace_usfm_for_p_m_c_ts_tag_line_space.py):
  \\ts\\*    exactly ONE blank line above (extra blank lines above it are removed), on its
           own line, above the \\p / \\m / \\q# / \\b (and heading) lines that precede
           the verse; nothing below
  \\p, \\m   blank line above unless the line above is \\ts\\*, \\c, \\b (or blank)
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# folder that holds the en_ult / en_ust checkouts (default: the parent of this tool)
ROOT = os.path.abspath(os.environ.get("UW_CONTENT_DIR") or os.path.dirname(HERE))
sys.path.insert(0, HERE)

from usfm_markers import strip_alignment  # noqa: E402

TS_LINE = "\\ts\\*"
# marker-only lines that belong to the start of the paragraph a \ts\* goes above
_STACK = re.compile(r"^\\(p|m|q\d?|pi\d?|nb|li\d?|pm|pmo|pmc|pmr|mi|pc|b|qa|s\d?|ms\d?|sp|r)(\s|$)")
_MARKER_ONLY = re.compile(r"^\\[a-z0-9]+\*?\s*$")
_V_TOKEN = re.compile(r"\\v\s+(\d+(?:-\d+)?)(?=\s|$)")
_C_LINE = re.compile(r"^\\c\s+(\d+)\s*$")
_WORD = re.compile(r"[\w’'-]+", re.U)
_BOUNDARY = re.compile(r"(?:[.?!:;][\u201d\u2019\"')\]}]*|[,.?!;:\u2014][\u201d\u2019]+[\u201d\u2019\"')\]}]*|,)$")


class PlaceError(Exception):
    pass


def norm_word(w):
    return re.sub(r"[^\w]", "", w.lower())


class UsfmDoc:
    def __init__(self, text):
        self.lines = text.split("\n")
        self.inserts = []     # (line_index, [new lines])
        self.moves = []       # (line_index, trailing text to move from line i-1 to line i)
        self.deletes = set()  # extra blank lines to drop (above a restored \ts\*)
        self._index()

    # ------------------------------------------------------------------ index
    def _index(self):
        """chapter line numbers, and for each verse the line holding its \\v token."""
        self.chapter_line = {}
        self.verse_line = {}      # (ch, "V") -> line index
        self.verse_end = {}       # (ch, "V") -> line index of next \v/\c line (exclusive)
        self.has_intro = set()    # chapters with a \d before \v 1
        ch, last = None, None
        for i, line in enumerate(self.lines):
            m = _C_LINE.match(line.strip())
            if m:
                ch = int(m.group(1))
                self.chapter_line[ch] = i
                if last:
                    self.verse_end[last] = i
                last = None
                continue
            if ch is None:
                continue
            if line.startswith("\\d") and (ch, "1") not in self.verse_line and "\\v " not in line:
                self.has_intro.add(ch)
            for vm in _V_TOKEN.finditer(line):
                key = (ch, vm.group(1))
                if key not in self.verse_line:
                    if last:
                        self.verse_end[last] = i
                    self.verse_line[key] = i
                    last = key
        if last:
            self.verse_end[last] = len(self.lines)

    def find_verse(self, ch, v):
        if (ch, v) in self.verse_line:
            return (ch, v)
        if "-" in v and (ch, v.split("-")[0]) in self.verse_line:   # catalog bridge, now split
            return (ch, v.split("-")[0])
        for (c, vv) in self.verse_line:          # key "5" inside a range "4-5"
            if c == ch and "-" in vv:
                a, b = vv.split("-")
                if int(a) <= int(v.split("-")[0]) <= int(b):
                    return (c, vv)
        raise PlaceError(f"verse {ch}:{v} not found in current file")

    # --------------------------------------------------------------- helpers
    def _blank_or(self, i, *prefixes):
        if i < 0:
            return True
        t = self.lines[i].strip()
        return t == "" or any(t == p or t.startswith(p + " ") for p in prefixes)

    def _verse_line_prefix_ok(self, i):
        """The \\v token must start its line, or follow only paragraph markers."""
        line = self.lines[i]
        pre = line[:_V_TOKEN.search(line).start()].strip()
        return pre == "" or all(_STACK.match(tok) for tok in pre.split())

    def _walk_back_stack(self, i):
        """From line i, go back over marker-only paragraph/heading lines."""
        j = i
        while j - 1 >= 0 and _STACK.match(self.lines[j - 1].strip()) and \
                _MARKER_ONLY.match(self.lines[j - 1].strip()):
            j -= 1
        return j

    # ------------------------------------------------------- placement: \ts\*
    def place_ts(self, ch, v, sub, anchor=None):
        if not sub and (v == "0" or (v == "1" and ch not in self.has_intro)):
            if ch not in self.chapter_line:
                raise PlaceError(f"\\c {ch} not found")
            at = self.chapter_line[ch]
        elif not sub:
            key = self.find_verse(ch, v)
            vl = self.verse_line[key]
            if not self._verse_line_prefix_ok(vl):
                raise PlaceError(f"{ch}:{v}: text before \\v on its line; cannot place a line above it")
            at = self._walk_back_stack(vl)
        else:
            at = self._mid_verse_line(ch, v, sub, anchor)
            at = self._walk_back_stack(at)
        # exactly one blank line above
        j = at - 1
        while j >= 0 and self.lines[j].strip() == "":
            j -= 1
        blanks = at - 1 - j
        if blanks == 0:
            self.inserts.append((at, ["", TS_LINE]))
        else:
            self.deletes.update(range(j + 1, at - 1))   # keep one
            self.inserts.append((at, [TS_LINE]))
        return at

    # ---------------------------------------------------- placement: \p / \m
    def place_para(self, marker, ch, v, sub, anchor=None):
        if not sub:
            key = self.find_verse(ch, v)
            at = self.verse_line[key]
            if not self._verse_line_prefix_ok(at):
                raise PlaceError(f"{ch}:{v}: text before \\v on its line")
            if self.lines[at].lstrip().startswith("\\q"):
                raise PlaceError(f"{ch}:{v}: verse starts with a poetry marker")
        else:
            at = self._mid_verse_line(ch, v, sub, anchor)
        above = self.lines[at - 1].strip() if at > 0 else ""
        if marker == "m" and above == "\\p" or marker == "p" and above == "\\m":
            raise PlaceError(f"{ch}:{v}: \\{marker} would sit directly below a \\{above[1:]}")
        new = [] if self._blank_or(at - 1, TS_LINE, "\\c", "\\b") else [""]
        self.inserts.append((at, new + ["\\" + marker]))
        return at

    # ------------------------------------------------------------ mid-verse
    def _mid_verse_line(self, ch, v, sub, anchor):
        if not anchor or not anchor.get("after"):
            raise PlaceError(f"{ch}:{v}{sub}: no anchor words in catalog")
        key = self.find_verse(ch, v)
        start, end = self.verse_line[key], self.verse_end[key]
        words = []   # (normalized word, raw word, line index, first word on its line?)
        for i in range(start, end):
            seg = self.lines[i]
            if i == start:
                seg = seg[_V_TOKEN.search(seg).end():]
            plain = strip_alignment(seg)
            plain = re.sub(r"\\[a-z0-9+]+\*?", " ", plain)
            toks = [t for t in plain.split() if norm_word(t)]
            for n, t in enumerate(toks):
                words.append((norm_word(t), t, i, n == 0))
        target = [norm_word(w) for w in anchor["after"].split()
                  if norm_word(w) and "=" not in w and '"' not in w][:4]   # skip alignment debris
        if not target:
            raise PlaceError(f"{ch}:{v}{sub}: empty anchor")
        hits = [k for k in range(1, len(words) - len(target) + 1)
                if [w[0] for w in words[k:k + len(target)]] == target]
        good = [k for k in hits if _BOUNDARY.search(words[k - 1][1]) or anchor.get("poetry_line")]
        if not good:
            what = "not found" if not hits else "found, but not after sentence punctuation"
            raise PlaceError(f"{ch}:{v}{sub}: anchor words \"{' '.join(anchor['after'].split()[:4])}\" {what}")
        if len(good) > 1:
            raise PlaceError(f"{ch}:{v}{sub}: anchor words match {len(good)} places in the verse")
        k = good[0]
        _, _, line_i, first = words[k]
        if line_i == start:
            raise PlaceError(f"{ch}:{v}{sub}: break falls on the \\v line (would need a line split)")
        if not first:
            raise PlaceError(f"{ch}:{v}{sub}: other words share the line before the break (would need a line split)")
        depth = 0
        for i in range(start, line_i):
            depth += self.lines[i].count("\\zaln-s") - self.lines[i].count("\\zaln-e")
        if depth != 0:
            raise PlaceError(f"{ch}:{v}{sub}: break falls inside an alignment span (\\zaln-s … \\zaln-e)")
        # an opening quote left dangling at the end of the previous line moves down
        prev = self.lines[line_i - 1]
        mq = re.search(r"(?<=\\\*)(\s*[\u201c\u2018]+\s*)$", prev)
        if mq:
            self.moves.append((line_i, mq.group(1)))
        return line_i

    # ---------------------------------------------------------------- output
    def render(self):
        lines = list(self.lines)
        for line_i, q in sorted(set(self.moves), reverse=True):
            lines[line_i - 1] = lines[line_i - 1][: len(lines[line_i - 1]) - len(q)]
            lines[line_i] = q.strip() + lines[line_i]
        by_line = {}
        for at, new in self.inserts:
            by_line.setdefault(at, []).append(new)
        out = []
        for i in range(len(lines) + 1):
            if i in by_line:
                block = [x for new in by_line[i] for x in new]
                has_blank = "" in block
                # \ts\* first, then \p / \m; at most one blank line, at the top
                block = ([""] if has_blank else []) + \
                        sorted((x for x in block if x), key=lambda x: 0 if x == TS_LINE else 1)
                if has_blank and out and out[-1].strip() == "":
                    block = block[1:]
                out += block
            if i < len(lines) and i not in self.deletes:
                out.append(lines[i])
        return "\n".join(out)


def only_insertions(before, after, added):
    """Verify `after` is `before` plus exactly `added` marker lines ({"ts": n, "p": n, "m": n})
    and blank lines: the text with all \\ts\\*/\\p/\\m and blank lines removed (and line
    breaks/spaces ignored, since a quote may move between lines) must be identical."""
    marks = {"ts": TS_LINE, "p": "\\p", "m": "\\m"}

    def body(t):
        return "".join(l for l in t.split("\n") if l.strip() not in marks.values()).replace(" ", "")

    def count(t, m):
        return sum(1 for l in t.split("\n") if l.strip() == marks[m])

    if body(before) != body(after):
        return False
    return all(count(after, m) - count(before, m) == added.get(m, 0) for m in marks)
