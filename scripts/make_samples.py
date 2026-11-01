"""Generate tagged sample PDFs with known accessibility problems.

Usage: python scripts/make_samples.py samples

Embeds Arial from /System/Library/Fonts/Supplemental, so it runs on macOS only.
"""

from __future__ import annotations

import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pikepdf
from pikepdf import Array, Dictionary, Name, String

FONT_DIR = Path("/System/Library/Fonts/Supplemental")
PAGE_W, PAGE_H = 612, 792
MARGIN = 72


# --------------------------------------------------------------------------- fonts

class TTF:
    """Just enough TrueType parsing to embed a simple WinAnsi font."""

    def __init__(self, path: Path, ps_name: str, bold: bool):
        self.data = path.read_bytes()
        self.ps_name = ps_name
        self.bold = bold
        d = self.data
        n = struct.unpack(">H", d[4:6])[0]
        self.tables = {}
        for i in range(n):
            tag, _, off, ln = struct.unpack(">4sIII", d[12 + 16 * i: 28 + 16 * i])
            self.tables[tag.decode()] = (off, ln)
        head = self.tables["head"][0]
        self.upem = struct.unpack(">H", d[head + 18: head + 20])[0]
        self.bbox = struct.unpack(">hhhh", d[head + 36: head + 44])
        hhea = self.tables["hhea"][0]
        self.ascent, self.descent = struct.unpack(">hh", d[hhea + 4: hhea + 8])
        self.n_hmetrics = struct.unpack(">H", d[hhea + 34: hhea + 36])[0]
        os2 = self.tables["OS/2"][0]
        self.cap_height = struct.unpack(">h", d[os2 + 88: os2 + 90])[0]
        post = self.tables["post"][0]
        self.italic_angle = struct.unpack(">i", d[post + 4: post + 8])[0] / 65536
        self.cmap = self._cmap()
        self.widths = {}
        for code in range(32, 256):
            try:
                ch = bytes([code]).decode("cp1252")
            except UnicodeDecodeError:
                continue
            gid = self.cmap.get(ord(ch), 0)
            self.widths[code] = self._advance(gid) * 1000 // self.upem

    def _advance(self, gid: int) -> int:
        hmtx = self.tables["hmtx"][0]
        gid = min(gid, self.n_hmetrics - 1)
        return struct.unpack(">H", self.data[hmtx + 4 * gid: hmtx + 4 * gid + 2])[0]

    def _cmap(self) -> dict[int, int]:
        d = self.data
        base = self.tables["cmap"][0]
        n = struct.unpack(">H", d[base + 2: base + 4])[0]
        for i in range(n):
            plat, enc, off = struct.unpack(">HHI", d[base + 4 + 8 * i: base + 12 + 8 * i])
            if (plat, enc) == (3, 1):
                sub = base + off
                break
        else:
            raise ValueError("no (3,1) cmap")
        assert struct.unpack(">H", d[sub: sub + 2])[0] == 4
        seg = struct.unpack(">H", d[sub + 6: sub + 8])[0] // 2
        ends = struct.unpack(f">{seg}H", d[sub + 14: sub + 14 + 2 * seg])
        p = sub + 16 + 2 * seg
        starts = struct.unpack(f">{seg}H", d[p: p + 2 * seg])
        deltas = struct.unpack(f">{seg}h", d[p + 2 * seg: p + 4 * seg])
        ro_pos = p + 4 * seg
        ranges = struct.unpack(f">{seg}H", d[ro_pos: ro_pos + 2 * seg])
        out = {}
        for i in range(seg):
            for c in range(starts[i], ends[i] + 1):
                if c == 0xFFFF:
                    continue
                if ranges[i] == 0:
                    g = (c + deltas[i]) & 0xFFFF
                else:
                    gp = ro_pos + 2 * i + ranges[i] + 2 * (c - starts[i])
                    g = struct.unpack(">H", d[gp: gp + 2])[0]
                    if g:
                        g = (g + deltas[i]) & 0xFFFF
                out[c] = g
        return out

    def width(self, text: str, size: float) -> float:
        return sum(self.widths.get(b, 0) for b in text.encode("cp1252")) * size / 1000

    def to_pdf(self, pdf: pikepdf.Pdf) -> pikepdf.Object:
        s = 1000 / self.upem
        fd = Dictionary(
            Type=Name.FontDescriptor, FontName=Name("/" + self.ps_name),
            Flags=32 | (1 << 18 if self.bold else 0),
            FontBBox=Array([round(v * s) for v in self.bbox]),
            ItalicAngle=self.italic_angle, Ascent=round(self.ascent * s),
            Descent=round(self.descent * s), CapHeight=round(self.cap_height * s),
            StemV=120 if self.bold else 80,
            FontWeight=700 if self.bold else 400,
            FontFile2=pdf.make_stream(self.data, Length1=len(self.data)),
        )
        return pdf.make_indirect(Dictionary(
            Type=Name.Font, Subtype=Name.TrueType, BaseFont=Name("/" + self.ps_name),
            FirstChar=32, LastChar=255,
            Widths=Array([self.widths.get(c, 0) for c in range(32, 256)]),
            Encoding=Name.WinAnsiEncoding, FontDescriptor=pdf.make_indirect(fd),
        ))


REGULAR = TTF(FONT_DIR / "Arial.ttf", "ArialMT", bold=False)
BOLD = TTF(FONT_DIR / "Arial Bold.ttf", "Arial-BoldMT", bold=True)


def pdf_str(text: str, encoding: str = "cp1252") -> bytes:
    raw = text.encode(encoding)
    return b"(" + raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)") + b")"


# ------------------------------------------------------------------ model

@dataclass
class MC:
    """A marked-content sequence on a page (or in a form XObject on that page)."""
    page: "Page"
    mcid: int
    form: "Form | None" = None
    exists: bool = True


@dataclass
class E:
    type: str
    kids: list = field(default_factory=list)
    attrs: dict = field(default_factory=dict)


class Form:
    def __init__(self, page: "Page"):
        self.page = page
        self.ops: list[bytes] = []
        self.next_mcid = 0
        self.parents: dict[int, E] = {}
        self.name = f"Fm{len(page.forms)}"


class Page:
    def __init__(self, doc: "Doc"):
        self.doc = doc
        self.ops: list[bytes] = []
        self.next_mcid = 0
        self.y = PAGE_H - MARGIN
        self.forms: list[Form] = []

    # -- low level
    def _target(self, in_form: bool):
        if not in_form:
            return self
        if not self.forms:
            self.forms.append(Form(self))
        return self.forms[-1]

    def _mc(self, tag: str, body: bytes, in_form=False) -> MC:
        t = self._target(in_form)
        mcid = t.next_mcid
        t.next_mcid += 1
        t.ops.append(b"/%s <</MCID %d>> BDC\n%s\nEMC\n" % (tag.encode(), mcid, body))
        return MC(self, mcid, t if in_form else None)

    def _font(self, bold: bool) -> tuple[bytes, TTF]:
        return (b"/FB", BOLD) if bold else (b"/FR", REGULAR)

    def _text_ops(self, lines: list[str], size: float, bold: bool, x: float, y: float,
                  leading: float, scale: float = 1.0) -> bytes:
        fname, _ = self._font(bold)
        enc = self.doc.encoding
        if scale != 1.0:
            ops = [b"BT %s %g Tf %g 0 0 %g %g %g Tm" % (fname, size / scale, scale, scale, x, y)]
            lead = leading / scale
        else:
            ops = [b"BT %s %g Tf %g %g Td" % (fname, size, x, y)]
            lead = leading
        for i, line in enumerate(lines):
            if i:
                ops.append(b"0 %g Td" % -lead)
            ops.append(pdf_str(line, enc) + b" Tj")
        ops.append(b"ET")
        return b" ".join(ops)

    def _wrap(self, text: str, size: float, bold: bool, width: float) -> list[str]:
        font = self._font(bold)[1]
        lines, cur = [], ""
        for word in text.split():
            cand = f"{cur} {word}".strip()
            if cur and font.width(cand, size) > width:
                lines.append(cur)
                cur = word
            else:
                cur = cand
        return lines + [cur] if cur else lines

    # -- high level
    def text(self, tag: str, text: str, size=11, bold=False, x=MARGIN, gap=None,
             in_form=False, scale=1.0, width=None, **attrs) -> E:
        """A wrapped text block in its own marked-content sequence."""
        width = width or PAGE_W - MARGIN - x
        lines = self._wrap(text, size, bold, width)
        leading = size * 1.3
        self.y -= size
        body = self._text_ops(lines, size, bold, x, self.y, leading, scale)
        self.y -= leading * (len(lines) - 1) + (gap if gap is not None else size * 0.8)
        return E(tag, [self._mc(tag, body, in_form)], attrs)

    def runs(self, tag: str, runs: list[tuple], size=11, x=MARGIN, gap=None) -> E:
        """One line of mixed runs: (text, bold, child_tag|None). Child tags become kids."""
        self.y -= size
        kids = []
        for text, bold, child in runs:
            body = self._text_ops([text], size, bold, x, self.y, 0)
            mc = self._mc(child or tag, body)
            kids.append(E(child, [mc]) if child else mc)
            x += self._font(bold)[1].width(text, size)
        self.y -= gap if gap is not None else size * 0.8
        return E(tag, kids)

    def artifact(self, text: str, y: float, size=9, x=MARGIN):
        body = self._text_ops([text], size, False, x, y, 0)
        self.ops.append(b"/Artifact <</Type /Pagination>> BDC\n%s\nEMC\n" % body)

    def untagged(self, text: str, y: float, size=9, x=MARGIN, bold=False):
        self.ops.append(self._text_ops([text], size, bold, x, y, 0) + b"\n")

    def empty_mc(self, tag: str = "P") -> MC:
        return self._mc(tag, b"")

    def chart(self, tag="Figure", height=120, **attrs) -> E:
        """A small vector bar chart."""
        top = self.y - 10
        base = top - height
        bars = [0.45, 0.7, 0.55, 0.9, 0.65]
        ops = [b"q 0.2 0.4 0.7 rg"]
        for i, h in enumerate(bars):
            ops.append(b"%g %g 40 %g re f" % (MARGIN + 20 + i * 60, base, h * height))
        ops.append(b"0 0 0 RG 1 w %g %g m %g %g l S Q" % (MARGIN + 10, base, MARGIN + 320, base))
        self.y = base - 20
        return E(tag, [self._mc(tag, b" ".join(ops))], attrs)

    def space(self, pts: float):
        self.y -= pts


class Doc:
    def __init__(self, title: str | None, lang: str | None = "en-US", ua=True,
                 rolemap: dict | None = None, encoding: str = "cp1252"):
        self.title = title
        self.lang = lang
        self.ua = ua
        self.rolemap = rolemap or {}
        self.encoding = encoding
        self.pages: list[Page] = []
        self.inherit_pg = False

    def page(self, header: str | None = None, footer=True) -> Page:
        p = Page(self)
        self.pages.append(p)
        if header:
            p.artifact(header, PAGE_H - 40)
        if footer:
            p.artifact(f"Page {len(self.pages)}", 36, x=PAGE_W / 2 - 15)
        return p

    # -- serialisation
    def save(self, path: Path, root: E | None):
        pdf = pikepdf.new()
        fonts = Dictionary(FR=REGULAR.to_pdf(pdf), FB=BOLD.to_pdf(pdf))
        self._fonts = fonts
        page_objs = {}
        form_objs = {}
        next_key = 0
        self._keys = {}
        for p in self.pages:
            page = pdf.add_blank_page(page_size=(PAGE_W, PAGE_H))
            res = Dictionary(Font=fonts)
            xobjs = Dictionary()
            ops = list(p.ops)
            for f in p.forms:
                fo = pdf.make_stream(b"".join(f.ops), Type=Name.XObject, Subtype=Name.Form,
                                     BBox=Array([0, 0, PAGE_W, PAGE_H]),
                                     Resources=Dictionary(Font=fonts),
                                     StructParents=next_key + 1)
                xobjs["/" + f.name] = fo
                form_objs[id(f)] = fo
                ops.append(b"q /%s Do Q\n" % f.name.encode())
            if xobjs:
                res.XObject = xobjs
            page.obj.Resources = res
            page.obj.Contents = pdf.make_stream(b"".join(ops))
            page.obj.StructParents = next_key
            self._keys[id(p)] = next_key
            for f in p.forms:
                self._keys[id(f)] = next_key + 1
                next_key += 1
            next_key += 1
            page_objs[id(p)] = page.obj
        self._page_objs = page_objs
        self._form_objs = form_objs

        if root is not None:
            self._parent_tree: dict[int, dict[int, pikepdf.Object]] = {}
            sroot = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
            doc_elem = self._build(pdf, root, sroot, None)
            sroot.K = doc_elem
            nums = Array()
            for key in sorted(self._parent_tree):
                arr = self._parent_tree[key]
                nums.append(key)
                nums.append(pdf.make_indirect(Array(
                    [arr.get(i, pikepdf.Object.parse(b"null")) for i in range(max(arr) + 1)])))
            sroot.ParentTree = pdf.make_indirect(Dictionary(Nums=nums))
            sroot.ParentTreeNextKey = next_key
            if self.rolemap:
                sroot.RoleMap = Dictionary({"/" + k: Name("/" + v) for k, v in self.rolemap.items()})
            pdf.Root.StructTreeRoot = sroot
            pdf.Root.MarkInfo = Dictionary(Marked=True)

        if self.lang:
            pdf.Root.Lang = String(self.lang)
        if self.title:
            pdf.docinfo[Name.Title] = self.title
            pdf.Root.ViewerPreferences = Dictionary(DisplayDocTitle=True)
        xmp = _xmp(self.title, self.ua)
        pdf.Root.Metadata = pdf.make_stream(xmp, Type=Name.Metadata, Subtype=Name.XML)
        pdf.save(path)

    def _build(self, pdf, node: E, parent, parent_pg) -> pikepdf.Object:
        elem = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name("/" + node.type), P=parent))
        for k, v in node.attrs.items():
            if k == "A":
                elem.A = v
            else:
                elem[Name("/" + k)] = String(v) if isinstance(v, str) else v
        first = next(iter(_mcs(node)), None)
        pg = self._page_objs[id(first.page)] if first else None
        if pg is not None and not (self.inherit_pg and parent_pg is not None
                                   and pg.objgen == parent_pg.objgen):
            elem.Pg = pg
        eff_pg = pg if pg is not None else parent_pg
        kids = Array()
        for kid in node.kids:
            if isinstance(kid, E):
                kids.append(self._build(pdf, kid, elem, eff_pg))
                continue
            kpg = self._page_objs[id(kid.page)]
            if kid.form is None and eff_pg is not None and kpg.objgen == eff_pg.objgen:
                kids.append(kid.mcid)
            else:
                mcr = Dictionary(Type=Name.MCR, Pg=kpg, MCID=kid.mcid)
                if kid.form is not None:
                    mcr.Stm = self._form_objs[id(kid.form)]
                kids.append(mcr)
            if kid.exists:
                key = self._keys[id(kid.form if kid.form is not None else kid.page)]
                self._parent_tree.setdefault(key, {})[kid.mcid] = elem
        if len(kids) == 1:
            elem.K = kids[0]
        elif kids:
            elem.K = kids
        return elem


def _mcs(node: E):
    for k in node.kids:
        if isinstance(k, E):
            yield from _mcs(k)
        else:
            yield k


def _xmp(title: str | None, ua: bool) -> bytes:
    t = f"<dc:title><rdf:Alt><rdf:li xml:lang=\"x-default\">{title}</rdf:li></rdf:Alt></dc:title>" if title else ""
    u = "<pdfuaid:part>1</pdfuaid:part>" if ua else ""
    return f"""<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/"
     xmlns:pdfuaid="http://www.aiim.org/pdfua/ns/id/">{t}{u}</rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
<?xpacket end="w"?>""".encode("utf-8")


# ----------------------------------------------------------------- samples

LOREM = ("Accessible documents let everyone read the same information, including people "
         "who use screen readers, magnifiers or braille displays. The structure tree tells "
         "assistive technology what each piece of content is and in which order to read it.")
LOREM2 = ("Each section below describes one part of the annual review. Figures are rounded "
          "to the nearest whole number, and all amounts are in thousands of euros.")
TH_SCOPE = Dictionary(O=Name.Table, Scope=Name.Column)


def _list(p: Page, items: list[str]) -> E:
    lis = []
    for it in items:
        p.y -= 11
        lbl = p._mc("Lbl", p._text_ops(["•"], 11, False, MARGIN + 10, p.y, 0))
        body = p._mc("LBody", p._text_ops([it], 11, False, MARGIN + 25, p.y, 0))
        p.y -= 5
        lis.append(E("LI", [E("Lbl", [lbl]), E("LBody", [body])]))
    p.y -= 8
    return E("L", lis)


def _table(p: Page, rows: list[list[str]], header=True, cell_tags=None) -> E:
    trs = []
    for r, row in enumerate(rows):
        p.y -= 11
        cells = []
        for c, val in enumerate(row):
            is_head = header and r == 0
            tag = "TH" if is_head else "TD"
            if cell_tags:
                tag = cell_tags
            mc = p._mc(tag, p._text_ops([val], 11, is_head, MARGIN + c * 130, p.y, 0))
            cells.append(E(tag, [mc], {"A": TH_SCOPE} if tag == "TH" else {}))
        p.y -= 7
        trs.append(E("TR", cells))
    p.y -= 8
    return E("Table", trs)


def clean_report(out: Path):
    """Well-formed baseline: should (nearly) pass veraPDF PDF/UA-1."""
    d = Doc("Annual Accessibility Review 2026")
    p = d.page(header="Annual Accessibility Review 2026")
    kids = [
        p.text("H1", "Annual Accessibility Review 2026", size=24, bold=True),
        p.text("P", LOREM),
        p.text("H2", "1. Summary", size=16, bold=True),
        p.text("P", LOREM2),
        p.text("H3", "1.1 Key findings", size=13, bold=True),
        _list(p, ["Most documents were tagged.", "Headings were often wrong.",
                  "Few figures had alternative text."]),
        p.text("H2", "2. Results", size=16, bold=True),
        _table(p, [["Quarter", "Documents", "Passed"], ["Q1", "120", "64"],
                   ["Q2", "135", "88"], ["Q3", "142", "101"]]),
        p.chart(Alt="Bar chart of passed documents per month, rising from 45 to 90 percent."),
    ]
    p2 = d.page(header="Annual Accessibility Review 2026")
    kids += [
        p2.text("H2", "3. Next steps", size=16, bold=True),
        p2.text("P", LOREM),
        p2.text("P", "Kontakt os, hvis du har spørgsmål til rapporten.", Lang="da-DK"),
    ]
    d.save(out, E("Document", kids))


def fake_headings(out: Path):
    """Visual headings tagged as P, Span and NonStruct, plus bold body text decoys."""
    d = Doc("Fake headings")
    p = d.page()
    kids = [
        p.text("P", "Project Handbook", size=24, bold=True),          # should be H1
        p.text("P", LOREM),
        E("P", [E("Span", p.text("Span", "Getting started", size=16, bold=True).kids)]),  # H2
        p.text("P", LOREM2),
        E("NonStruct", p.text("NonStruct", "Installation", size=13, bold=True).kids),  # H3
        p.text("P", LOREM),
        p.text("P", "Configuration", size=11, bold=True),            # bold, body size: likely H3
        p.text("P", LOREM2),
        # decoys: bold but not headings
        p.runs("P", [("Note: ", True, "Span"), ("back up your files before you upgrade.", False, None)]),
        p.text("P", "This whole paragraph is set in bold because it is an important warning "
                    "that readers must not miss, but it is still body text and not a heading.",
               bold=True),
        p.text("P", "Large quote text in a pull quote, not a heading.", size=16),  # large, not bold
        p.text("H2", "Troubleshooting", size=16, bold=True),         # already correct
        p.text("P", LOREM),
    ]
    d.save(out, E("Document", kids))


def heading_hierarchy(out: Path):
    """Starts with H2, two H1s, H1 -> H3 skip, H2 -> H4 skip."""
    d = Doc("Broken heading hierarchy")
    p = d.page()
    kids = [
        p.text("H2", "Introduction", size=16, bold=True),
        p.text("P", LOREM),
        p.text("H1", "Policy on Digital Accessibility", size=24, bold=True),
        p.text("P", LOREM2),
        p.text("H3", "Scope", size=13, bold=True),           # skips H2
        p.text("P", LOREM),
        p.text("H1", "Responsibilities", size=24, bold=True),  # second H1
        p.text("H2", "Managers", size=16, bold=True),
        p.text("H4", "Training", size=11, bold=True),        # skips H3
        p.text("P", LOREM2),
        p.text("H6", "Appendix", size=16, bold=True),        # looks like H2, tagged H6
    ]
    d.save(out, E("Document", kids))


def figures_alt(out: Path):
    """Figures without /Alt, with empty /Alt, and one correct."""
    d = Doc("Figures and alternative text")
    p = d.page()
    kids = [
        p.text("H1", "Figures", size=24, bold=True),
        p.text("P", "The first chart has no alternative text at all."),
        p.chart(height=90),
        p.text("P", "The second chart has an empty alternative text."),
        p.chart(height=90, Alt=""),
        p.text("P", "The third chart is described correctly."),
        p.chart(height=90, Alt="Bar chart showing sales per region; region four is highest."),
    ]
    p2 = d.page()
    kids += [
        p2.text("P", "A chart nested inside a Div without alternative text:"),
        E("Div", [p2.chart(height=90)]),
        p2.text("P", "A decorative chart that should probably be an artifact:"),
        p2.chart(height=40),
    ]
    d.save(out, E("Document", kids))


def missing_lang(out: Path):
    """No /Lang on the catalog; foreign-language passages without /Lang."""
    d = Doc("Missing language", lang=None)
    p = d.page()
    kids = [
        p.text("H1", "Language information", size=24, bold=True),
        p.text("P", LOREM),
        p.text("P", "Dette afsnit er skrevet på dansk, men har ingen sprogangivelse."),
        p.text("P", "Dieser Absatz ist auf Deutsch, aber hat keine Sprachangabe."),
        p.text("P", "Ce paragraphe est en français et indique correctement sa langue.", Lang="fr-FR"),
    ]
    d.save(out, E("Document", kids))


def content_problems(out: Path):
    """MCIDs without content, empty marked content, untagged non-artifact content."""
    d = Doc("Content problems")
    p = d.page(footer=False)
    p.untagged("Content problems - internal draft", PAGE_H - 40)   # header, not artifact
    p.untagged("Page 1 of 1", 36, x=PAGE_W / 2 - 25)               # footer, not artifact
    h = p.text("H1", "Marked content problems", size=24, bold=True)
    body = p.text("P", LOREM)
    empty = p.empty_mc("P")
    p.text("P", "This paragraph is marked content, but no structure element points to it.")
    p.space(10)
    p.untagged("This paragraph is not marked at all and is not an artifact.", p.y, size=11)
    kids = [
        h, body,
        E("P", [MC(p, 42, exists=False)]),          # MCID 42 does not exist
        E("P", [empty]),                            # MCID exists but is empty
        E("Figure", [MC(p, 43, exists=False)], {"Alt": "Logo"}),
    ]
    d.save(out, E("Document", kids))


def lists_tables(out: Path):
    """Lists and tables with wrong structure."""
    d = Doc("Lists and tables")
    p = d.page()
    kids = [p.text("H1", "Lists and tables", size=24, bold=True),
            p.text("H2", "A list tagged as paragraphs", size=16, bold=True)]
    for it in ["• First item", "• Second item", "• Third item"]:
        kids.append(p.text("P", it, gap=4, x=MARGIN + 10))
    p.space(8)
    kids.append(p.text("H2", "An L with P children instead of LI", size=16, bold=True))
    kids.append(E("L", [p.text("P", f"{i}. Item number {i}", gap=4, x=MARGIN + 10) for i in (1, 2, 3)]))
    p.space(8)
    kids.append(p.text("H2", "LI without Lbl/LBody", size=16, bold=True))
    kids.append(E("L", [E("LI", p.text("LI", f"- Loose item {c}", gap=4, x=MARGIN + 10).kids)
                        for c in "abc"]))
    p.space(8)
    kids.append(p.text("H2", "A table without TR or TH", size=16, bold=True))
    t = _table(p, [["Name", "Role"], ["Ada", "Engineer"], ["Linus", "Maintainer"]],
               header=True, cell_tags="TD")
    kids.append(E("Table", [cell for tr in t.kids for cell in tr.kids]))  # TD directly in Table
    kids.append(p.text("H2", "A table tagged as paragraphs", size=16, bold=True))
    for row in [["Item", "Price"], ["Coffee", "3.00"], ["Tea", "2.50"]]:
        kids.append(p.runs("P", [(row[0], False, None), ("                    " + row[1], False, None)],
                           gap=6))
    d.save(out, E("Document", kids))


def rolemap_forms_multipage(out: Path):
    """Custom RoleMap types, content in a Form XObject, inherited /Pg, 3 pages, scaled text."""
    d = Doc("RoleMap, Form XObjects and multiple pages",
            rolemap={"Title": "H1", "Heading": "H2", "Body": "P", "Callout": "Span"})
    d.inherit_pg = True
    pages = [d.page(header="Handbook") for _ in range(3)]
    p1, p2, p3 = pages
    sect1 = E("Sect", [
        p1.text("Title", "Employee Handbook", size=24, bold=True, scale=2.0),  # 12pt * 2 via Tm
        p1.text("Body", LOREM),
        p1.text("Heading", "Working hours", size=16, bold=True, in_form=True),
        p1.text("Body", LOREM2, in_form=True),
    ])
    sect2 = E("Sect", [
        p2.text("Heading", "Holidays", size=16, bold=True),
        p2.runs("Body", [("Remember: ", True, "Callout"), ("request leave two weeks ahead.", False, None)]),
        p2.text("Body", LOREM),
    ])
    # One paragraph that continues from page 2 to page 3
    cont_a = p2.text("Body", "This paragraph starts at the bottom of page two and")
    cont_b = p3.text("Body", "continues at the top of page three, as one structure element.")
    sect2.kids.append(E("Body", cont_a.kids + cont_b.kids))
    sect3 = E("Sect", [p3.text("Heading", "Contact", size=16, bold=True),
                       p3.text("Body", "Write to hr@example.com for questions.")])
    d.save(out, E("Document", [sect1, sect2, sect3]))


def untagged(out: Path):
    """No structure tree at all: should be rejected."""
    d = Doc("Untagged", ua=False)
    p = d.page()
    p.text("P", "Untagged heading", size=24, bold=True)
    p.text("P", LOREM)
    d.save(out, None)


def danish_report(out: Path):
    """Realistic Danish document mixing several problems."""
    d = Doc("Årsrapport 2026", lang="da-DK")
    p = d.page(header="Kommunens årsrapport 2026")
    kids = [
        p.text("P", "Årsrapport 2026", size=26, bold=True),               # fake H1
        p.text("P", "Kommunen har i år arbejdet målrettet med digital tilgængelighed. "
                    "Rapporten beskriver resultater, udfordringer og næste skridt."),
        p.text("H3", "Økonomi", size=16, bold=True),                      # H1 -> H3 skip
        p.text("P", "Udgifterne til drift steg med 4 procent, primært på grund af højere "
                    "lønomkostninger og nye licenser."),
        p.chart(height=100),                                              # no Alt
        p.text("P", "Figur 1: Udgifter pr. kvartal", size=9),
        E("Span", p.text("Span", "Medarbejdere", size=16, bold=True).kids),  # fake H2
        _table(p, [["Afdeling", "Ansatte", "Ændring"], ["Teknik", "54", "+3"],
                   ["Social", "112", "-2"], ["Kultur", "23", "0"]]),
        p.text("P", "This sentence is in English but inherits the Danish language."),
    ]
    p2 = d.page(header="Kommunens årsrapport 2026", footer=False)
    p2.untagged("Side 2", 36, x=PAGE_W / 2 - 15)                          # untagged footer
    kids += [
        p2.text("H2", "Næste skridt", size=16, bold=True),
        _list(p2, ["Uddanne medarbejdere i tilgængelige dokumenter.",
                   "Gennemgå alle PDF-skabeloner.", "Måle fremskridt hvert kvartal."]),
        E("P", [MC(p2, 99, exists=False)]),
    ]
    d.save(out, E("Document", kids))


SAMPLES = {
    "01_clean_report.pdf": clean_report,
    "02_fake_headings.pdf": fake_headings,
    "03_heading_hierarchy.pdf": heading_hierarchy,
    "04_figures_alt.pdf": figures_alt,
    "05_missing_lang.pdf": missing_lang,
    "06_content_problems.pdf": content_problems,
    "07_lists_tables.pdf": lists_tables,
    "08_rolemap_forms_multipage.pdf": rolemap_forms_multipage,
    "09_untagged.pdf": untagged,
    "10_danish_report.pdf": danish_report,
}


if __name__ == "__main__":
    out_dir = Path(sys.argv[1])
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, fn in SAMPLES.items():
        fn(out_dir / name)
        print("wrote", out_dir / name)
