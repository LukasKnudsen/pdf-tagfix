"""Walk page content streams and collect text runs per marked-content ID (MCID)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import pikepdf

from .fonts import FontInfo

# (page objgen, form XObject objgen or None, MCID)
ContentKey = tuple[tuple[int, int], tuple[int, int] | None, int]

Matrix = tuple[float, float, float, float, float, float]
IDENTITY: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)

# TJ adjustments more negative than this (thousandths of an em) count as a space
_TJ_SPACE_THRESHOLD = -200


def _mul(m: Matrix, n: Matrix) -> Matrix:
    a, b, c, d, e, f = m
    A, B, C, D, E, F = n
    return (a * A + b * C, a * B + b * D,
            c * A + d * C, c * B + d * D,
            e * A + f * C + E, e * B + f * D + F)


def _matrix(operands) -> Matrix:
    return tuple(float(x) for x in operands)  # type: ignore[return-value]


@dataclass
class TextRun:
    text: str
    size: float
    font: str
    bold: bool


@dataclass
class _GState:
    ctm: Matrix = IDENTITY
    font: FontInfo | None = None
    font_size: float = 0.0
    render_mode: int = 0


@dataclass
class _MarkedContent:
    tag: str
    mcid: int | None
    key: ContentKey | None


@dataclass
class PageContent:
    """Text runs found in the content streams, grouped by MCID."""

    runs: dict[ContentKey, list[TextRun]] = field(default_factory=dict)


class ContentCollector:
    def __init__(self) -> None:
        self._fonts: dict[tuple[int, int], FontInfo] = {}
        self._active_forms: set[tuple[int, int]] = set()
        self.result = PageContent()

    def collect_page(self, page: pikepdf.Page) -> None:
        resources = _inherited(page.obj, "/Resources")
        page_id = page.obj.objgen
        self._walk(page.obj, resources, page_id, None, _GState(), [])

    # --- internals -------------------------------------------------------

    def _font(self, resources, name) -> FontInfo | None:
        fonts = resources.get("/Font") if resources is not None else None
        if fonts is None or name not in fonts:
            return None
        fdict = fonts[name]
        key = fdict.objgen if fdict.is_indirect else (id(fdict), 0)
        if key not in self._fonts:
            self._fonts[key] = FontInfo(fdict)
        return self._fonts[key]

    def _walk(self, stream, resources, page_id, form_id, gs: _GState,
              mc_stack: list[_MarkedContent]) -> None:
        try:
            instructions = pikepdf.parse_content_stream(stream)
        except pikepdf.PdfError:
            return

        gs_stack: list[_GState] = []
        tm: Matrix = IDENTITY
        newline = False
        base_depth = len(mc_stack)

        for instr in instructions:
            if isinstance(instr, pikepdf.ContentStreamInlineImage):
                continue
            op = str(instr.operator)
            args = instr.operands

            if op == "q":
                gs_stack.append(_GState(gs.ctm, gs.font, gs.font_size, gs.render_mode))
            elif op == "Q":
                if gs_stack:
                    gs = gs_stack.pop()
            elif op == "cm":
                gs.ctm = _mul(_matrix(args), gs.ctm)
            elif op == "BT":
                tm = IDENTITY
            elif op == "Tm":
                tm = _matrix(args)
                newline = True
            elif op in ("Td", "TD"):
                if float(args[1]) != 0:
                    newline = True
            elif op == "T*":
                newline = True
            elif op == "Tf":
                gs.font = self._font(resources, args[0])
                gs.font_size = float(args[1])
            elif op == "Tr":
                gs.render_mode = int(args[0])
            elif op in ("BDC", "BMC"):
                mcid = None
                if op == "BDC":
                    props = args[1]
                    if isinstance(props, pikepdf.Name):
                        props = (resources.get("/Properties", {}) or {}).get(props)
                    if isinstance(props, pikepdf.Dictionary) and "/MCID" in props:
                        mcid = int(props.MCID)
                key = (page_id, form_id, mcid) if mcid is not None else None
                if key is not None:
                    # Register the MCID even if it turns out to hold no text (images, paths).
                    self.result.runs.setdefault(key, [])
                mc_stack.append(_MarkedContent(str(args[0]), mcid, key))
            elif op == "EMC":
                if len(mc_stack) > base_depth:
                    mc_stack.pop()
            elif op in ("Tj", "'", '"', "TJ"):
                if op in ("'", '"'):
                    newline = True
                text = self._decode(gs.font, args[-1], op == "TJ")
                self._emit(text, gs, tm, mc_stack, newline)
                newline = False
            elif op == "Do":
                self._do_xobject(resources, args[0], page_id, gs, mc_stack)

        del mc_stack[base_depth:]

    def _do_xobject(self, resources, name, page_id, gs, mc_stack) -> None:
        xobjs = resources.get("/XObject") if resources is not None else None
        if xobjs is None or name not in xobjs:
            return
        xobj = xobjs[name]
        if str(xobj.get("/Subtype", "")) != "/Form" or xobj.objgen in self._active_forms:
            return
        matrix = _matrix(xobj.get("/Matrix", list(IDENTITY)))
        sub_gs = _GState(_mul(matrix, gs.ctm), gs.font, gs.font_size, gs.render_mode)
        sub_res = xobj.get("/Resources", resources)
        self._active_forms.add(xobj.objgen)
        try:
            self._walk(xobj, sub_res, page_id, xobj.objgen, sub_gs, mc_stack)
        finally:
            self._active_forms.discard(xobj.objgen)

    @staticmethod
    def _decode(font: FontInfo | None, operand, is_tj: bool) -> str:
        def dec(s) -> str:
            raw = bytes(s)
            return font.decode(raw) if font else raw.decode("latin-1")

        if not is_tj:
            return dec(operand)
        parts = []
        for item in operand:
            if isinstance(item, pikepdf.String):
                parts.append(dec(item))
            elif float(item) < _TJ_SPACE_THRESHOLD:
                parts.append(" ")
        return "".join(parts)

    def _emit(self, text, gs: _GState, tm: Matrix, mc_stack, newline: bool) -> None:
        # The innermost enclosing sequence with an MCID owns the content.
        key = next((mc.key for mc in reversed(mc_stack) if mc.key is not None), None)
        if key is None or not text:
            return
        m = _mul(tm, gs.ctm)
        size = abs(gs.font_size) * math.hypot(m[2], m[3])
        font = gs.font
        bold = (font.bold if font else False) or gs.render_mode == 2
        runs = self.result.runs.setdefault(key, [])
        if newline and runs and not runs[-1].text.endswith((" ", "\n")) \
                and not text.startswith(" "):
            text = " " + text
        runs.append(TextRun(text, round(size, 2), font.name if font else "?", bold))


def _inherited(obj, key):
    """Look up a page attribute, following /Parent for inherited values."""
    seen = set()
    while obj is not None and obj.objgen not in seen:
        seen.add(obj.objgen)
        if key in obj:
            return obj[key]
        obj = obj.get("/Parent")
    return None


def collect_content(pdf: pikepdf.Pdf) -> PageContent:
    collector = ContentCollector()
    for page in pdf.pages:
        collector.collect_page(page)
    return collector.result
