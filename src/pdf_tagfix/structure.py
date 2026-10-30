"""Read the logical structure tree (StructTreeRoot) and attach content to it."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

import pikepdf

from .content import ContentKey, PageContent, TextRun, collect_content


def font_sizes(runs: list[TextRun]) -> Counter[float]:
    """Font sizes weighted by number of non-space characters."""
    sizes: Counter[float] = Counter()
    for r in runs:
        sizes[r.size] += len(r.text.strip())
    return +sizes


def bold_state(runs: list[TextRun]) -> str | None:
    """'fed', 'ikke-fed', 'delvis fed' or None when there is no text."""
    bold = sum(len(r.text.strip()) for r in runs if r.bold)
    total = sum(len(r.text.strip()) for r in runs)
    if total == 0:
        return None
    if bold == total:
        return "fed"
    if bold == 0:
        return "ikke-fed"
    return "delvis fed"


@dataclass
class ContentRef:
    """A marked-content reference (MCID) from a structure element."""

    page: int | None  # 1-based page number
    mcid: int
    key: ContentKey | None
    runs: list[TextRun] = field(default_factory=list)
    found: bool = False


@dataclass
class ObjectRef:
    """An object reference (OBJR), typically an annotation such as a link."""

    page: int | None
    subtype: str


@dataclass
class StructNode:
    type: str                     # /S as written in the file
    role: str | None              # standard type via RoleMap, if different
    page: int | None
    alt: str | None = None
    actual_text: str | None = None
    lang: str | None = None
    title: str | None = None
    kids: list[StructNode | ContentRef | ObjectRef] = field(default_factory=list)

    @property
    def runs(self) -> list[TextRun]:
        """Text runs from this element's own MCIDs (not from child elements)."""
        return [r for k in self.kids if isinstance(k, ContentRef) for r in k.runs]

    @property
    def text(self) -> str:
        return " ".join("".join(r.text for r in self.runs).split())

    def font_sizes(self) -> Counter[float]:
        return font_sizes(self.runs)

    def bold_state(self) -> str | None:
        return bold_state(self.runs)


@dataclass
class StructTree:
    root: StructNode | None
    lang: str | None
    unreferenced: list[ContentKey]   # MCIDs in content that no element points to
    missing: list[ContentRef]        # MCIDs referenced by elements but not found


class NoStructTree(Exception):
    pass


def _text(obj) -> str | None:
    return str(obj) if obj is not None else None


def _resolve_role(role_map, name: str) -> str | None:
    """Follow the RoleMap chain from a custom type to a standard type."""
    seen = {name}
    current = name
    while role_map is not None and pikepdf.Name(current) in role_map:
        target = str(role_map[pikepdf.Name(current)])
        if target in seen:
            break
        seen.add(target)
        current = target
    return current if current != name else None


class _Builder:
    def __init__(self, pdf: pikepdf.Pdf, content: PageContent, role_map):
        self.content = content
        self.role_map = role_map
        self.page_numbers = {p.obj.objgen: i + 1 for i, p in enumerate(pdf.pages)}
        self.referenced: set[ContentKey] = set()
        self.missing: list[ContentRef] = []
        self.visited: set[tuple[int, int]] = set()

    def element(self, elem: pikepdf.Dictionary, inherited_pg) -> StructNode | None:
        if elem.is_indirect:
            if elem.objgen in self.visited:
                return None
            self.visited.add(elem.objgen)

        s_type = str(elem.get("/S", "/?"))[1:]
        pg = elem.get("/Pg", inherited_pg)
        node = StructNode(
            type=s_type,
            role=(r[1:] if (r := _resolve_role(self.role_map, "/" + s_type)) else None),
            page=self._page_no(pg),
            alt=_text(elem.get("/Alt")),
            actual_text=_text(elem.get("/ActualText")),
            lang=_text(elem.get("/Lang")),
            title=_text(elem.get("/T")),
        )
        kids = elem.get("/K")
        if kids is None:
            return node
        if not isinstance(kids, pikepdf.Array):
            kids = [kids]
        for kid in kids:
            child = self.kid(kid, pg)
            if child is not None:
                node.kids.append(child)
        return node

    def kid(self, kid, pg):
        if isinstance(kid, int):
            return self.mcid_ref(int(kid), pg, None)
        if not isinstance(kid, pikepdf.Dictionary):
            return None
        kind = str(kid.get("/Type", ""))
        if kind == "/MCR":
            stm = kid.get("/Stm")
            return self.mcid_ref(int(kid.MCID), kid.get("/Pg", pg),
                                 stm.objgen if stm is not None else None)
        if kind == "/OBJR":
            obj = kid.get("/Obj")
            subtype = str(obj.get("/Subtype", "/?"))[1:] if isinstance(obj, pikepdf.Dictionary) else "?"
            return ObjectRef(self._page_no(kid.get("/Pg", pg)), subtype)
        return self.element(kid, pg)

    def mcid_ref(self, mcid: int, pg, stm_id) -> ContentRef:
        key = (pg.objgen, stm_id, mcid) if pg is not None else None
        runs = self.content.runs.get(key, []) if key else []
        found = key in self.content.runs if key else False
        ref = ContentRef(self._page_no(pg), mcid, key, runs, found)
        if key:
            self.referenced.add(key)
        if not found:
            self.missing.append(ref)
        return ref

    def _page_no(self, pg) -> int | None:
        return self.page_numbers.get(pg.objgen) if pg is not None else None


def read_struct_tree(pdf: pikepdf.Pdf) -> StructTree:
    root = pdf.Root.get("/StructTreeRoot")
    if root is None:
        raise NoStructTree("PDF'en har ingen tags (/StructTreeRoot mangler)")

    content = collect_content(pdf)
    builder = _Builder(pdf, content, root.get("/RoleMap"))

    # The StructTreeRoot itself is not an element; wrap its kids in a virtual root.
    top = StructNode(type="StructTreeRoot", role=None, page=None)
    kids = root.get("/K")
    if kids is not None:
        if not isinstance(kids, pikepdf.Array):
            kids = [kids]
        for kid in kids:
            child = builder.kid(kid, None)
            if child is not None:
                top.kids.append(child)

    unreferenced = sorted((k for k in content.runs if k not in builder.referenced),
                          key=lambda k: (k[0], k[1] or (0, 0), k[2]))
    return StructTree(
        root=top,
        lang=_text(pdf.Root.get("/Lang")),
        unreferenced=unreferenced,
        missing=builder.missing,
    )
