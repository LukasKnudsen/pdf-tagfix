# pdf-tagfix

A tool that fixes accessibility problems in PDFs that already have tags. It doesn't create tags from scratch. Instead it analyses the existing tag structure and suggests fixes, for example text that looks like a heading but is tagged as `P`. A person reviews the suggestions, and the tool writes the approved fixes back to the PDF. Finally, the result is validated with veraPDF.

Status: Still early stage. So far the tool can only print the tag tree.

## Why: the European Accessibility Act

The [European Accessibility Act](https://eur-lex.europa.eu/eli/dir/2019/882/oj) (EAA, Directive (EU) 2019/882) has applied since 28 June 2025. It requires many products and services sold in the EU to be accessible, including the documents they provide, such as PDFs. The harmonised standard EN 301 549 builds on WCAG. PDF/UA (ISO 14289) is the established standard for accessible PDFs.

pdf-tagfix helps fix common problems in tagged PDFs and checks the result against PDF/UA with veraPDF. A passing veraPDF report doesn't prove that a document meets the EAA. Some requirements, like meaningful alt text and a logical reading order, still need a person to check them.

## Installation

Requires Python 3.11+.

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install --only-binary=pikepdf -e ".[dev]"
```

`--only-binary=pikepdf` makes pip use a prebuilt wheel. Without it, pip may pick a newer version that only exists as source. The build then fails because the qpdf headers are missing. This happens on Intel Macs with Python 3.14, for example.

## Usage

### Print the tag tree

```sh
pdf-tagfix-tree samples/report.pdf
# or
python -m pdf_tagfix.dump_tree samples/report.pdf
```

Example output:

```
report.pdf  (1 pages, language: en)

Document  lang="en"
  H1
    NonStruct  p.1  "Annual report for the orchard"  [24pt, bold]
  P
    NonStruct  p.1  "This is body text with"  [12pt, not bold]
    Strong
      NonStruct  p.1  "bold text"  [12pt, bold]
  NonStruct
    NonStruct  p.1  "Fake heading that is just bold text"  [16.5pt, bold]
  Figure  p.1  alt="A picture"

MCIDs without content: 0   untagged content (not referenced): 0
```

Each line shows:

- **Tag type.** A custom type is also shown with its standard type from the RoleMap, e.g. `Heading → H1`.
- **Page** (`p.1`).
- **Text** from the element's own marked-content sequences (MCIDs). Text in child elements appears on the child's own line.
- **Font size** in points, as it actually appears on the page (the `Tf` size multiplied by the text and transformation matrices). When sizes are mixed, the dominant one comes first, e.g. `12pt/9pt`.
- **Bold**: `bold`, `not bold` or `partly bold`. This is decided from the font name (Bold, Black, Semibold …), `/FontWeight`, the ForceBold flag, and "fake bold" (render mode 2).
- `alt`, `actualtext`, `lang` and `title`, if they are set.

Lines starting with `!` are MCIDs that the tag tree points to but that don't exist in the page content.

The last line counts MCIDs referenced by the tree but missing from the content (`MCIDs without content`) and content that no tag points to (`untagged content`).

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
