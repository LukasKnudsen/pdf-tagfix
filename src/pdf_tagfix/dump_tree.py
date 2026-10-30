"""Print the tag tree of a PDF with type, text, font size and bold for each element.

    python -m pdf_tagfix.dump_tree file.pdf
    pdf-tagfix-tree file.pdf --json
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys

import pikepdf

from .content import TextRun
from .structure import (ContentRef, NoStructTree, ObjectRef, StructNode, bold_state,
                        font_sizes, read_struct_tree)


def _fmt_size(size: float) -> str:
    return f"{size:g}pt"


def _shorten(text: str, limit: int) -> str:
    if limit and len(text) > limit:
        return text[: limit - 1] + "…"
    return text


def _style(runs: list[TextRun]) -> str:
    """'11pt, not bold' summary for a list of runs (dominant size first)."""
    sizes = font_sizes(runs)
    parts = []
    if sizes:
        parts.append("/".join(_fmt_size(s) for s, _ in sizes.most_common()))
    if bold := bold_state(runs):
        parts.append(bold)
    return ", ".join(parts)


def _print_node(node: StructNode, depth: int, args, out) -> None:
    indent = "  " * depth
    label = node.type + (f" → {node.role}" if node.role else "")
    bits = [label]
    if node.page is not None:
        bits.append(f"p.{node.page}")

    text = node.text
    if text:
        bits.append(json.dumps(_shorten(text, args.max_text), ensure_ascii=False))
        bits.append(f"[{_style(node.runs)}]")
    for attr, value in (("alt", node.alt), ("actualtext", node.actual_text),
                        ("lang", node.lang), ("title", node.title)):
        if value:
            bits.append(f"{attr}={json.dumps(_shorten(value, args.max_text), ensure_ascii=False)}")
    out.write(indent + "  ".join(bits) + "\n")

    for kid in node.kids:
        if isinstance(kid, StructNode):
            _print_node(kid, depth + 1, args, out)
        elif isinstance(kid, ObjectRef):
            out.write(f"{indent}  <OBJR {kid.subtype}>\n")
        elif isinstance(kid, ContentRef):
            if not kid.found:
                out.write(f"{indent}  ! MCID {kid.mcid} (p.{kid.page}) not found in page content\n")
            elif args.show_mcid:
                kid_text = "".join(r.text for r in kid.runs).strip()
                out.write(f"{indent}  · MCID {kid.mcid}: "
                          f"{json.dumps(_shorten(kid_text, args.max_text), ensure_ascii=False)}"
                          f" [{_style(kid.runs)}]\n")


def _to_json(node) -> dict:
    if isinstance(node, StructNode):
        sizes = node.font_sizes()
        return {
            "type": node.type,
            "role": node.role,
            "page": node.page,
            "text": node.text,
            "font_sizes": [s for s, _ in sizes.most_common()],
            "bold": node.bold_state(),
            "alt": node.alt,
            "actual_text": node.actual_text,
            "lang": node.lang,
            "title": node.title,
            "kids": [_to_json(k) for k in node.kids if not isinstance(k, ContentRef)],
            "mcids": [dataclasses.asdict(k) | {"key": None}
                      for k in node.kids if isinstance(k, ContentRef)],
        }
    if isinstance(node, ObjectRef):
        return {"objr": node.subtype, "page": node.page}
    raise TypeError(node)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Print the tag tree of a PDF with text, font size and bold/not bold.")
    parser.add_argument("pdf", help="path to a tagged PDF")
    parser.add_argument("--max-text", type=int, default=80, metavar="N",
                        help="truncate text to N characters (0 = no truncation, default 80)")
    parser.add_argument("--show-mcid", action="store_true",
                        help="show each MCID sequence separately under its element")
    parser.add_argument("--json", action="store_true", help="print the tree as JSON")
    args = parser.parse_args(argv)

    try:
        with pikepdf.open(args.pdf) as pdf:
            tree = read_struct_tree(pdf)
            n_pages = len(pdf.pages)
    except NoStructTree as exc:
        print(f"{args.pdf}: {exc}", file=sys.stderr)
        return 2
    except (pikepdf.PdfError, FileNotFoundError) as exc:
        print(f"{args.pdf}: cannot open: {exc}", file=sys.stderr)
        return 1

    if args.json:
        json.dump({
            "file": args.pdf,
            "pages": n_pages,
            "lang": tree.lang,
            "root": _to_json(tree.root),
            "missing_mcids": len(tree.missing),
            "unreferenced_mcids": len(tree.unreferenced),
        }, sys.stdout, ensure_ascii=False, indent=2)
        print()
        return 0

    out = sys.stdout
    out.write(f"{args.pdf}  ({n_pages} pages, language: {tree.lang or 'not set'})\n\n")
    for kid in tree.root.kids:
        if isinstance(kid, StructNode):
            _print_node(kid, 0, args, out)
    out.write("\n")
    out.write(f"MCIDs without content: {len(tree.missing)}   "
              f"untagged content (not referenced): {len(tree.unreferenced)}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
