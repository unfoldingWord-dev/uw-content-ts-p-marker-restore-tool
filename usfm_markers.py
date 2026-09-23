#!/usr/bin/env python3
"""
Shared helpers for cataloging / checking / restoring \\ts\\*, \\p and \\m markers.

- strip_alignment(): removes \\zaln-s/\\zaln-e milestones and unwraps \\w word|attrs\\w*
  (same result as usfm-alignment-remover: the English words are kept).
- parse_markers(): walks a USFM text and returns, per verse, the verse's plain
  text plus the structural markers found in it and where they sit.
- GitBlobReader: fast reader of a file at many commits (git cat-file --batch).
"""
import re
import subprocess

PARA_MARKERS = {"p", "m"}          # what we catalog/restore (besides ts)
POETRY_RE = re.compile(r"^q\d?$")  # \q, \q1, \q2, \q3 ...
# other paragraph-level markers that "own" a verse start (used for conflict checks)
OTHER_PARA = {"pi", "pi1", "pi2", "li", "li1", "li2", "nb", "pm", "pmo", "pmc", "pmr",
              "mi", "pc", "b"}
# heading-type markers: their text is not verse text
HEADINGS = {"d", "s", "s1", "s2", "s3", "s4", "ms", "ms1", "ms2", "mr", "sp", "qa",
            "r", "sr", "cl", "cd"}


_ZALN_S = re.compile(r"\\zaln-s\s*\|.*?\\\*")
_ZALN_E = re.compile(r"\\zaln-e\\\*")
_WORD = re.compile(r"\\w\s+([^|\\]*?)\|[^\\]*?\\w\*")
_WORD_NOATTR = re.compile(r"\\w\s+([^|\\]*?)\\w\*")
_KS = re.compile(r"\\k-[se]\s*(\|.*?)?\\\*")


def strip_alignment(usfm: str) -> str:
    """Remove alignment data, keeping the English words. Line structure is kept
    (each \\w line becomes a word line); use parse_markers() for the real text."""
    s = _ZALN_S.sub("", usfm)
    s = _ZALN_E.sub("", s)
    s = _KS.sub("", s)
    s = _WORD.sub(r"\1", s)
    s = _WORD_NOATTR.sub(r"\1", s)
    return s


def flatten_unaligned(usfm: str) -> str:
    """Strip alignment and rejoin word-per-line output into normal lines."""
    s = strip_alignment(usfm)
    out, buf = [], []
    for line in s.split("\n"):
        t = line.strip()
        if t.startswith("\\") and not t.startswith(("\\f", "\\x", "\\qs", "\\add", "\\nd")):
            if buf:
                out.append(_norm_space(" ".join(buf)))
                buf = []
            out.append(t)
        elif t:
            buf.append(t)
    if buf:
        out.append(_norm_space(" ".join(buf)))
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------
_TOKEN = re.compile(r"\\(\+?[A-Za-z0-9]+(?:-[se])?)(\\\*|\*)?")
_NOTE = re.compile(r"\\(f|x|fe)\s.*?\\\1\*", re.S)
# punctuation that does not change the text but can differ around a marker
# Places a paragraph / chunk break may legitimately follow ("sentence" ends):
#   . ? ! : ;  (optionally followed by closing quotes / brackets / braces)
#   punctuation + closing quote:        written,”  | straight,’   (\m after a quote)
#   comma before an opening quote:      he said, | “Men ...       (speech starts)
_CLOSE = "\u201d\u2019\"')\\]}"
_SENT_END = re.compile(
    r"(?:[.?!:;]+[" + _CLOSE + r"]*"
    r"|[,.?!;:\u2014][\u201d\u2019]+[" + _CLOSE + r"]*"
    r"|,(?=\s+[\u201c\u2018]))"
    r"(?=\s|$)")


def _norm_space(t):
    t = re.sub(r"\s+", " ", t)
    # a word line followed by punctuation line like ", " -> fix " ," artifacts
    t = re.sub(r" ([,.;:?!\u201d\u2019)\]])", r"\1", t)
    t = re.sub(r"([\u201c\u2018(\[]) ", r"\1", t)
    return t.strip()


class Verse:
    __slots__ = ("chapter", "verse", "text", "events")

    def __init__(self, chapter, verse):
        self.chapter = chapter
        self.verse = verse          # string, may be "3-4"
        self.text = ""              # plain text (alignment, notes removed)
        self.events = []            # (offset_in_text, marker)  offset 0 == before verse

    @property
    def ref(self):
        return f"{self.chapter}:{self.verse}"


def parse_markers(usfm: str):
    """Return list[Verse]. Markers between verses are attached to the following
    verse at offset 0; markers before \\c N attach to N:1 (first verse), unless
    the chapter has a \\d description before \\v 1: then the \\d is verse "0"
    (intro) and markers before the \\d attach to N:0, markers after it to N:1."""
    s = strip_alignment(usfm)
    s = _NOTE.sub(" ", s)
    verses = []
    chapter = None
    cur = None
    pending = []      # markers seen since last verse text (will go to next verse)
    text_parts = []

    def flush_text():
        if cur is not None and text_parts:
            cur.text = _norm_space(cur.text + " " + " ".join(text_parts))
            text_parts.clear()

    pos = 0
    header_done = False
    in_heading = False
    for m in _TOKEN.finditer(s):
        chunk = s[pos:m.start()]
        pos = m.end()
        if cur is not None and chunk.strip() and not in_heading:
            text_parts.append(chunk)
        tag = m.group(1)
        if tag in ("c", "v") or tag in PARA_MARKERS or POETRY_RE.match(tag) \
                or tag in OTHER_PARA or tag.startswith("ts") or tag == "s5":
            in_heading = False
        if tag == "c":
            num = re.match(r"\s*(\d+)", s[pos:])
            if num:
                pos += num.end()
                flush_text()
                chapter = int(num.group(1))
                header_done = True
                # anything pending since last verse text stays pending (\ts\* before \c)
                cur = None
                text_parts.clear()
            continue
        if tag == "v":
            num = re.match(r"\s*(\d+(?:-\d+)?)\s?", s[pos:])
            if num and chapter is not None:
                pos += num.end()
                flush_text()
                cur = Verse(chapter, num.group(1))
                verses.append(cur)
                for mk in pending:
                    cur.events.append((0, mk))
                pending = []
            continue
        base = tag.rstrip("*")
        if not header_done and base not in ("ts", "s5"):
            continue
        if base in ("ts", "ts-s", "ts-e", "s5"):   # \s5 is the old chunk marker
            mk = "ts"
        elif base in HEADINGS:
            in_heading = True
            mk = base
            if base == "d" and cur is None and chapter is not None \
                    and not any(v.chapter == chapter for v in verses[-1:]):
                v0 = Verse(chapter, "0")
                v0.events = [(0, p) for p in pending]
                verses.append(v0)
                pending = []
                continue
        elif base in PARA_MARKERS or POETRY_RE.match(base) or base in OTHER_PARA:
            mk = base
        else:
            continue  # character markers (\add, \nd, \qs ...) are ignored
        if cur is None:
            pending.append(mk)
        else:
            # is there verse text after this marker before the next \v / \c ?
            flush_text()
            cur_text_len = len(cur.text)
            if cur_text_len == 0:
                cur.events.append((0, mk))
            else:
                # tentatively mid-verse; resolved after we know if text follows
                cur.events.append((cur_text_len, mk))
    flush_text()
    # markers recorded at end of a verse's text (no text after them) belong to next verse
    for i, v in enumerate(verses):
        keep, move = [], []
        for off, mk in v.events:
            while 0 < off < len(v.text) and v.text[off] == " ":
                off += 1
            if off > 0 and off >= len(v.text):
                move.append(mk)
            else:
                keep.append((off, mk))
        v.events = keep
        if move and i + 1 < len(verses):
            verses[i + 1].events[0:0] = [(0, mk) for mk in move]
    return verses


def sentence_index(text: str, offset: int) -> int:
    """0-based index of the sentence that begins at/after `offset`."""
    return sum(1 for m in _SENT_END.finditer(text) if m.end() <= offset)


def split_sentences(text: str):
    """Return list of (start_offset, sentence_text)."""
    out, start = [], 0
    for m in _SENT_END.finditer(text):
        end = m.end()
        out.append((start, text[start:end].strip()))
        start = end
        while start < len(text) and text[start] == " ":
            start += 1
    if start < len(text):
        out.append((start, text[start:].strip()))
    return out


def letter(i: int) -> str:
    return "abcdefghijklmnopqrstuvwxyz"[i] if i < 26 else f"_{i}"


# ---------------------------------------------------------------------------
# git
# ---------------------------------------------------------------------------
class GitBlobReader:
    def __init__(self, repo):
        self.repo = repo
        self.proc = subprocess.Popen(["git", "-C", repo, "cat-file", "--batch"],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE)

    def read(self, commit, path):
        self.proc.stdin.write(f"{commit}:{path}\n".encode())
        self.proc.stdin.flush()
        header = self.proc.stdout.readline().decode()
        if header.endswith("missing\n"):
            return None
        size = int(header.split()[2])
        data = self.proc.stdout.read(size)
        self.proc.stdout.read(1)
        return data.decode("utf-8", errors="replace")

    def close(self):
        self.proc.stdin.close()
        self.proc.wait()


def file_history(repo, path, first_parent=True):
    """[(sha, iso_date, author, subject)] newest first."""
    cmd = ["git", "-C", repo, "log", "--format=%H%x1f%aI%x1f%an%x1f%s"]
    if first_parent:
        cmd.append("--first-parent")
    cmd += ["--", path]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    return [tuple(l.split("\x1f")) for l in out.splitlines() if l]
