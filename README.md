# pdf-tagfix

A tool that fixes accessibility problems in PDFs that **already have tags**. It
doesn't create tags from scratch. Instead it analyses the existing tag structure and
suggests fixes, for example text that looks like a heading but is tagged as `P`.
A person reviews the suggestions, and the tool writes the approved fixes back to the
PDF. Finally, the result is validated with veraPDF (PDF/UA).

Status: early stage. So far the tool can only print the tag tree.

## Installation

Requires Python 3.11+.

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install --only-binary=pikepdf -e ".[dev]"
```

`--only-binary=pikepdf` makes pip use a prebuilt wheel. Without it, pip may pick a
newer version that only exists as source. The build then fails because the qpdf
headers are missing. This happens on Intel Macs with Python 3.14, for example.

## Usage

### Print the tag tree

```sh
pdf-tagfix-tree samples/report.pdf
# or
python -m pdf_tagfix.dump_tree samples/report.pdf
```

Example output (the tool's labels are in Danish):

```
report.pdf  (1 sider, sprog: da)

Document  lang="da"
  H1
    NonStruct  s.1  "Årsrapport for æblehaven"  [24pt, fed]
  P
    NonStruct  s.1  "Dette er brødtekst med"  [12pt, ikke-fed]
    Strong
      NonStruct  s.1  "fed tekst"  [12pt, fed]
  NonStruct
    NonStruct  s.1  "Falsk overskrift som bare er fed tekst"  [16.5pt, fed]
  Figure  s.1  alt="Et billede"

MCID'er uden indhold: 0   indhold uden tag (ikke refereret): 0
```

Each line shows:

- **Tag type.** A custom type is also shown with its standard type from the RoleMap, e.g. `Overskrift → H1`.
- **Page** (`s.` = side, page).
- **Text** from the element's own marked-content sequences (MCIDs). Text in child
  elements appears on the child's own line.
- **Font size** in points, as it actually appears on the page (the `Tf` size
  multiplied by the text and transformation matrices). When sizes are mixed, the
  dominant one comes first, e.g. `12pt/9pt`.
- **Bold**: `fed` (bold), `ikke-fed` (not bold) or `delvis fed` (partly bold). This
  is decided from the font name (Bold, Black, Semibold …), `/FontWeight`, the
  ForceBold flag, and "fake bold" (render mode 2).
- `alt`, `actualtext`, `lang` and `titel` (title), if they are set.

Lines starting with `!` are MCIDs that the tag tree points to but that don't exist
in the page content.

The last line counts MCIDs referenced by the tree but missing from the content
(`MCID'er uden indhold`) and content that no tag points to (`indhold uden tag`).

Options:

| Option | Meaning |
|--------|---------|
| `--max-text N` | truncate text to N characters (0 = no truncation, default 80) |
| `--show-mcid` | show each MCID sequence separately, with its own size and weight |
| `--json` | print the tree as JSON (for further processing) |

### Validating with veraPDF

```sh
~/verapdf/verapdf --flavour ua1 --format text file.pdf
```

## Tests

```sh
pytest
```

The tests build their own PDFs and don't need any files in `samples/`.

## Folders

- `src/pdf_tagfix/` holds the code.
- `tests/` holds the tests.
- `samples/` is for your own test PDFs. It's in `.gitignore` because the PDFs may contain confidential data.
