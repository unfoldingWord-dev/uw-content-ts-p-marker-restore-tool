# uw-content-ts-p-marker-restore-tool

Tools to catalog, check and restore the `\ts\*` (translation section / chunk) markers
and the `\p` / `\m` paragraph markers in the unfoldingWord **en_ult** and **en_ust**
USFM files.

These markers get lost when books are edited in tools that don't keep them, like the
Bible Editor (BE) app and older translationCore round trips. The tool keeps a
**catalog** of where each marker belongs in every book. You can then:

- see what is missing in the current files,
- put the missing `\ts\*` back automatically, and
- restore missing `\p` / `\m` by how severe the loss is in each chapter.

The catalogs in `catalog/` are included in this repo. They were built on 2026-09-23
from en_ult `b32e8ffa9` and en_ust `f8fa4f0a`, after every book's `\ts\*` had been
restored and checked. You can use them right away to repair future damage without
rebuilding anything.

## Setup

**Requirements:** Python 3.8 or newer (tested with 3.14), and `git`. There are no packages to install.

**Folder layout.** Clone this repo **next to** your `en_ult` and `en_ust` checkouts:

```
some-folder/
├── en_ult/                                  git clone https://git.door43.org/unfoldingWord/en_ult.git
├── en_ust/                                  git clone https://git.door43.org/unfoldingWord/en_ust.git
└── uw-content-ts-p-marker-restore-tool/     this repo
```

The scripts look for `../en_ult` and `../en_ust` from this folder. If your checkouts
live somewhere else, point the scripts at the folder that contains them:

```bash
export UW_CONTENT_DIR=/path/to/folder/containing/en_ult_and_en_ust
```

**Full history is required.** `en_ult` and `en_ust` must be full clones. Don't use
`--depth`: the survey reads each book's whole git history, including side branches,
to find good older copies and to detect Bible Editor commits.

**Where the scripts write.** They read and write the USFM files in the working trees
of `en_ult` / `en_ust`. Commit or stash your own changes first, then review the
result with `git diff` before you commit it.

## Normal workflow

After a Bible Editor merge (or any edit) has removed markers:

```bash
cd uw-content-ts-p-marker-restore-tool
git -C ../en_ult pull && git -C ../en_ust pull

python3 check_markers.py                             # what is missing, all books
python3 restore_missing_ts_markers.py ult --dry-run  # preview
python3 restore_missing_ts_markers.py ult            # put missing \ts\* back (all ULT books)
python3 restore_missing_ts_markers.py ust

python3 restore_missing_pm_markers.py ult --list     # \p / \m losses by chapter severity
python3 restore_missing_pm_markers.py ult --severity large
```

Review with `git -C ../en_ult diff`, then commit.

**Keep the catalogs current.** When editors *deliberately* change chunking or
paragraphs, and the markers are otherwise complete, rebuild the catalogs. That way
the restore doesn't undo their work (see [Building the catalogs](#building-the-catalogs)).

## Scripts

Every script takes the translation (`ult` or `ust`). Book codes are case-insensitive
(`zec`, `ZEC`).

### Check and list

| Command | What it does |
|---|---|
| `python3 check_markers.py [--repo en_ult en_ust] [--book ZEC …]` | Compares every catalog with the current files. Writes `report/missing_report.md` (a summary table) and `report/missing_report.json` (every reference). |
| `python3 list_missing_ts.py ult 1ti [--text]` | Missing `\ts\*` by chapter, with the catalog's source copy and the commit that last removed them. |
| `python3 list_unexplained.py ust zec [--text]` | Missing `\p`/`\m` with nothing replacing them. |
| `python3 list_moved.py ust zec [--text]` | Missing `\p`/`\m` that look moved (a *new* `\p`/`\m` in the verse before or after) or changed (a `\q#` or other paragraph marker in or around the verse). |

`--text` also prints the start of each verse.

### Restore `\ts\*`

```bash
python3 restore_missing_ts_markers.py ult                   # every ULT book with missing \ts\*
python3 restore_missing_ts_markers.py ust --dry-run         # report only
python3 restore_missing_ts_markers.py ult zec isa           # just these books
python3 restore_missing_ts_markers.py ult zec --chapters 3 4
python3 restore_missing_ts_markers.py ult zec --output /tmp/38-ZEC.usfm   # write elsewhere
```

It prints one status line per book, then a total. It does **not** touch `\p` / `\m`.

- `N:0`, and `N:1` in a chapter without a `\d` intro, go **before `\c N`**. `N:1` in a
  chapter with a `\d` intro goes after the title, before `\v 1`.
- Every other `\ts\*` goes on its own line above the `\p` / `\m` / `\q#` / `\b` /
  heading lines that come before the verse. Mid-verse, it goes above the line where
  the recorded sentence starts.
- A restored `\ts\*` always has **exactly one blank line above it** (extra blank
  lines are removed) and nothing below it.
- If the old copy had `\v 8` or `\v 9` and the file now has `\v 8-9`, the marker goes
  before the bridge. A marker recorded at both 8 and 9 is placed once.
- Malformed chunk markers on their own line (`\ts`, `\ts*`, and the pre-2020 `\s5`)
  are rewritten as `\ts\*`.

A marker it can't place is printed as `ERROR: <ref>: <reason>`. The others are still
written, and the exit status is 1. Reasons include: the verse no longer exists;
mid-verse, the recorded words can't be found after sentence punctuation; or the break
would fall inside an alignment span or need a line split.

### Restore `\p` / `\m`, by chapter severity

```bash
python3 restore_missing_pm_markers.py ult --list                   # every book: chapters + severity
python3 restore_missing_pm_markers.py ult --severity large         # all books, "large" chapters only
python3 restore_missing_pm_markers.py ust --severity large moderate few --dry-run
python3 restore_missing_pm_markers.py ust zec --severity few --chapters 3 14
```

Only **unexplained** losses are restored: nothing moved or changed nearby (see
`list_moved.py`). Each chapter gets its own severity, because one editor may have
ignored paragraphs while another, in the next chapter, moved them on purpose.

| Severity | Rule for the chapter |
|---|---|
| `large` | 3 or more unexplained, and nothing moved or changed in that chapter |
| `moderate` | 2 or more unexplained (and not large) |
| `few` | exactly 1 unexplained |

- A `\p`/`\m` goes directly before the `\v` line (below any `\ts\*`), or before the
  recorded sentence mid-verse.
- It gets a blank line above unless the line above is `\ts\*`, `\c` or `\b`.
- If an opening quote was left at the end of the previous line, it moves down with
  the break.

**Safety checks (both restore scripts).**
- **Only additions:** the output must be the input plus `\ts\*`/`\p`/`\m` and blank
  lines. If anything else would change, that book is not written.
- **Placement read-back:** each placed marker is checked in the output, and a warning
  is printed if it doesn't read back as present.
- **Either order:** you can run the two scripts in either order; the result is the
  same.

## Building the catalogs

```bash
python3 survey_history.py                  # ~3 min, both repos -> survey/{ult,ust}_survey.json
python3 build_catalog.py                   # all books -> catalog/<res>_<book>_markers.json
python3 survey_history.py --repo en_ult --book PSA EST     # just some books
python3 build_catalog.py  --repo en_ult --book PSA EST
python3 build_catalog.py  --repo en_ult --book ZEC --commit <sha>   # force one source commit
```

Always run `survey_history.py` before `build_catalog.py`: the catalog builder reads
the survey JSON.

> **Warning:** a chapter that passes the verse-count test is cataloged from **the
> current file as it is**. So rebuild the catalogs only when the files are in a
> state you trust (markers complete, no wrong extras). Otherwise mistakes get
> recorded as correct, and the restore will put them back.

### Where each chapter's markers come from

**1. Current file or history?** A chunk is usually 2–6 verses, so a chapter of V
verses should have at least `ceil(V / 6)` `\ts\*` (and at least 1).
- A chapter that meets this is cataloged from the **current file**. So a short
  chapter with one `\ts\*`, like EST 10 or a short Psalm, is fine.
- A chapter with fewer gets a **history check**. The verse count only decides
  *whether* to check.

**2. The history check.** The script looks for the newest older copy of that chapter
with **more** `\ts\*` than today.
- BE sometimes removed markers a few per commit, so it keeps walking back while the
  count still rises, and takes the newest commit at that peak.
- If no older copy ever had more, the chapter is fine as it is and is listed in
  `chapters_checked_ok`.

**3. Bible Editor and bot text is never used as a source.**
- Every commit on any branch by `BW Bot`, or with `bible-editor` in its subject, is
  scanned. That covers "bible-editor export: …" and "Merge pull request
  'bible-editor: X ult → master'".
- A fingerprint of each chapter those commits changed is recorded. Any copy of a
  chapter with that exact text is rejected, whoever committed it (including merges
  that carried it onto master).
- A later human edit of that chapter produces new text, so it can be used.

**4. Other rules.**
- Commits with duplicated chapters or verses are skipped.
- `\s5`, the chunk marker used before 2020, counts as `\ts\*`.

### Overrides: `catalog_overrides.json`

```json
{"ult": {"ISA": {"ts_commit": "03b56ef6d", "why": "2022-12-05 chunking preferred"}}}
```

`ts_commit` takes every `\ts\*` for that book from one commit (or only for
`"ts_chapters": [..]`). `\p`/`\m` still come from the normal sources, and
`build_catalog.py` applies the override on every rebuild. ULT ISA uses the
2022-12-05 chunking. The current ULT ISA file already has it, so the override only
matters if that file loses those `\ts\*` again.

## Catalog format: `catalog/<res>_<book>_markers.json`

```json
{
 "resource": "ult", "book": "ZEC", "file": "38-ZEC.usfm",
 "chapter_baselines": {"1": "current", "2": "current"},
 "chapters_restored_from_history": [], "chapters_checked_ok": [],
 "baseline_commits": {"current": {"date": "...", "author": "...", "subject": "current working file (HEAD b32e8ffa9)", "chapters": [1, 2]}},
 "ts_override": null,
 "counts": {"ts": 94, "p": 35, "m": 3},
 "markers": {
   "1:1":  ["ts", "p"],
   "1:4":  ["ts", "p"],
   "1:6b": ["m"],
   "3:0":  ["ts"]
 },
 "anchors": {"1:6b": {"after_punct": "?", "before": "have they not overtaken your fathers?", "after": "So they repented and said, ‘Just"}},
 "skipped": [{"ref": "3:8", "marker": "p", "before": "...", "after": "..."}],
 "baseline_poetry_verses": ["9:1", "9:2"]
}
```

| Key | Meaning |
|---|---|
| `N:0` | Before a `\d` chapter description (intro). A `\ts\*` before `\c N` that is followed by `\d` goes here. |
| `N:1` | Before verse 1. A `\ts\*` before `\c N` goes here when there is no `\d`. |
| `N:V` | Before verse V. Markers are listed in document order, e.g. `["ts", "p"]`. |
| `N:Vb`, `N:Vc`… | Mid-verse, before the 2nd, 3rd… sentence of the verse. `anchors` holds the words on each side and `after_punct`, the punctuation the break follows. |

**What counts as a sentence boundary.** A mid-verse marker is cataloged only at a
"sentence" boundary:
- after `.` `?` `!` `:` `;`;
- after punctuation plus a closing quote (`\m` resuming after a quotation);
- at a comma before an opening quote (a speech starting);
- after a short (4 words or fewer) greeting at the start of a verse, like "Dear
  Theophilus,".

Closing `}` `)` `]` may follow any of these. A `\ts\*` at a `\q#` poetry line inside
a sentence is also kept. Markers that split a clause anywhere else (e.g. "Hezekiah ‖
from the king") are **not cataloged**; they are listed in `skipped`.

**Other rules:**
- Poetry `\q#` markers are never cataloged, since they are the editor's layout choice.
- Repeated identical markers at one spot are stored once.
- Heading text (`\d`, `\s#`, `\qa`, `\ms`, `\sp`, `\r`, `\cl`…) is not verse text.
- Alignment is stripped as `usfm-alignment-remover` does it: `\zaln-s…\*` and
  `\zaln-e\*` are dropped, and `\w word|…\w*` becomes `word`.

To correct a catalog entry by hand, edit or delete its key in `markers`. Running
`build_catalog.py` for that book overwrites the file.

## Files

| File | Purpose |
|---|---|
| `usfm_markers.py` | Alignment stripping, USFM marker parser, sentence splitting, fast git blob reader |
| `survey_history.py` | History scan, BE detection, per-chapter source selection → `survey/` |
| `build_catalog.py` | Writes `catalog/` (applies `catalog_overrides.json`) |
| `check_markers.py` | Catalog vs current file → `report/` |
| `list_missing_ts.py`, `list_unexplained.py`, `list_moved.py` | Per-book listings |
| `restore_common.py` | Placement engine shared by the restore scripts |
| `restore_missing_ts_markers.py`, `restore_missing_pm_markers.py` | Restore scripts |
| `catalog/` | The catalogs, one per translation and book (132 files) |
| `survey/` | Survey output that `build_catalog.py` reads |
