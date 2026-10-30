"""Font handling: decode string bytes to Unicode and detect bold weight."""

from __future__ import annotations

import re

import pikepdf

# FontDescriptor /Flags bit 19 (1-based) = ForceBold
_FORCE_BOLD = 1 << 18

_BOLD_NAME = re.compile(r"bold|black|heavy|semibold|demi|\bbd\b", re.IGNORECASE)
_SUBSET_PREFIX = re.compile(r"^[A-Z]{6}\+")

_HEX = re.compile(rb"<([0-9A-Fa-f\s]*)>")
_CMAP_TOKEN = re.compile(rb"<[0-9A-Fa-f\s]*>|\[|\]|[A-Za-z]+")

# Small subset of the Adobe Glyph List; enough for /Differences in typical
# Western (incl. Danish) documents. Unknown names fall back to the base encoding.
_GLYPH_NAMES = {
    "space": " ", "exclam": "!", "quotedbl": '"', "numbersign": "#",
    "dollar": "$", "percent": "%", "ampersand": "&", "quotesingle": "'",
    "quoteright": "’", "quoteleft": "‘", "parenleft": "(",
    "parenright": ")", "asterisk": "*", "plus": "+", "comma": ",",
    "hyphen": "-", "period": ".", "slash": "/", "colon": ":",
    "semicolon": ";", "less": "<", "equal": "=", "greater": ">",
    "question": "?", "at": "@", "bracketleft": "[", "backslash": "\\",
    "bracketright": "]", "underscore": "_", "braceleft": "{", "bar": "|",
    "braceright": "}", "endash": "–", "emdash": "—",
    "bullet": "•", "quotedblleft": "“", "quotedblright": "”",
    "ellipsis": "…", "fi": "fi", "fl": "fl", "ff": "ff", "ffi": "ffi",
    "ffl": "ffl", "ae": "æ", "AE": "Æ", "oslash": "ø", "Oslash": "Ø",
    "aring": "å", "Aring": "Å", "adieresis": "ä", "odieresis": "ö",
    "udieresis": "ü", "eacute": "é", "germandbls": "ß", "section": "§",
    "degree": "°", "copyright": "©", "registered": "®", "Euro": "€",
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
}


def _glyph_to_unicode(name: str) -> str | None:
    if name in _GLYPH_NAMES:
        return _GLYPH_NAMES[name]
    if len(name) == 1:
        return name
    m = re.fullmatch(r"uni([0-9A-Fa-f]{4})+", name)
    if m:
        hexpart = name[3:]
        return "".join(chr(int(hexpart[i:i + 4], 16)) for i in range(0, len(hexpart), 4))
    m = re.fullmatch(r"u([0-9A-Fa-f]{4,6})", name)
    if m:
        return chr(int(m.group(1), 16))
    return None


def _utf16(hexbytes: bytes) -> str:
    raw = bytes.fromhex(re.sub(rb"\s", b"", hexbytes).decode("ascii"))
    return raw.decode("utf-16-be", errors="replace")


class ToUnicodeCMap:
    """Minimal parser for /ToUnicode CMaps (bfchar, bfrange, codespacerange)."""

    def __init__(self, data: bytes):
        self.map: dict[bytes, str] = {}
        self.codespaces: list[tuple[bytes, bytes]] = []
        self._parse(data)

    def _parse(self, data: bytes) -> None:
        tokens = _CMAP_TOKEN.findall(data)
        i = 0
        mode = None
        while i < len(tokens):
            tok = tokens[i]
            if tok in (b"begincodespacerange", b"beginbfchar", b"beginbfrange"):
                mode = tok[5:]
                i += 1
                continue
            if tok.startswith(b"end"):
                mode = None
                i += 1
                continue
            if mode == b"codespacerange" and i + 1 < len(tokens):
                self.codespaces.append((_hexbytes(tok), _hexbytes(tokens[i + 1])))
                i += 2
            elif mode == b"bfchar" and i + 1 < len(tokens):
                self.map[_hexbytes(tok)] = _utf16(tokens[i + 1][1:-1])
                i += 2
            elif mode == b"bfrange" and i + 2 < len(tokens):
                lo, hi = _hexbytes(tok), _hexbytes(tokens[i + 1])
                i += 2
                width = len(lo)
                lo_n, hi_n = int.from_bytes(lo, "big"), int.from_bytes(hi, "big")
                if tokens[i] == b"[":
                    i += 1
                    n = lo_n
                    while i < len(tokens) and tokens[i] != b"]":
                        self.map[n.to_bytes(width, "big")] = _utf16(tokens[i][1:-1])
                        n += 1
                        i += 1
                    i += 1
                else:
                    dst = _hexbytes(tokens[i])
                    i += 1
                    base = int.from_bytes(dst, "big")
                    for off in range(min(hi_n - lo_n, 0xFFFF) + 1):
                        val = (base + off).to_bytes(len(dst), "big")
                        self.map[(lo_n + off).to_bytes(width, "big")] = val.decode(
                            "utf-16-be", errors="replace")
            else:
                i += 1

    def code_length(self, data: bytes, pos: int, default: int) -> int:
        """Byte length of the character code starting at data[pos]."""
        if not self.codespaces:
            return default
        for lo, hi in sorted(self.codespaces, key=lambda r: len(r[0])):
            n = len(lo)
            chunk = data[pos:pos + n]
            if len(chunk) == n and all(a <= b <= c for a, b, c in zip(lo, chunk, hi)):
                return n
        return default


def _hexbytes(tok: bytes) -> bytes:
    return bytes.fromhex(re.sub(rb"\s", b"", tok[1:-1]).decode("ascii"))


class FontInfo:
    """Decoding and style information for one PDF font dictionary."""

    def __init__(self, font: pikepdf.Dictionary):
        self.subtype = str(font.get("/Subtype", ""))
        base = str(font.get("/BaseFont", "/Unknown"))[1:]
        self.name = _SUBSET_PREFIX.sub("", base)
        self.composite = self.subtype == "/Type0"

        descriptor = font.get("/FontDescriptor")
        if self.composite:
            descendants = font.get("/DescendantFonts")
            if descendants is not None and len(descendants):
                descriptor = descendants[0].get("/FontDescriptor")

        self.bold = self._detect_bold(descriptor)

        self.cmap: ToUnicodeCMap | None = None
        tu = font.get("/ToUnicode")
        if isinstance(tu, pikepdf.Stream):
            try:
                self.cmap = ToUnicodeCMap(tu.read_bytes())
            except Exception:
                self.cmap = None

        self.simple_map = self._simple_encoding(font) if not self.composite else {}

    def _detect_bold(self, descriptor) -> bool:
        if _BOLD_NAME.search(self.name):
            return True
        if isinstance(descriptor, pikepdf.Dictionary):
            weight = descriptor.get("/FontWeight")
            if weight is not None and float(weight) >= 600:
                return True
            flags = descriptor.get("/Flags")
            if flags is not None and int(flags) & _FORCE_BOLD:
                return True
            fname = str(descriptor.get("/FontName", ""))
            if _BOLD_NAME.search(_SUBSET_PREFIX.sub("", fname[1:])):
                return True
        return False

    @staticmethod
    def _simple_encoding(font: pikepdf.Dictionary) -> dict[int, str]:
        base = "cp1252"
        enc = font.get("/Encoding")
        differences = None
        if isinstance(enc, pikepdf.Dictionary):
            if "/BaseEncoding" in enc:
                enc_name = str(enc.BaseEncoding)
                base = "mac_roman" if enc_name == "/MacRomanEncoding" else "cp1252"
            differences = enc.get("/Differences")
        elif isinstance(enc, pikepdf.Name) and str(enc) == "/MacRomanEncoding":
            base = "mac_roman"

        table = {}
        for code in range(256):
            try:
                table[code] = bytes([code]).decode(base)
            except UnicodeDecodeError:
                table[code] = "�"
        if differences is not None:
            code = 0
            for item in differences:
                if isinstance(item, pikepdf.Name):
                    uni = _glyph_to_unicode(str(item)[1:])
                    if uni is not None:
                        table[code] = uni
                    code += 1
                else:
                    code = int(item)
        return table

    def decode(self, data: bytes) -> str:
        default_len = 2 if self.composite else 1
        out = []
        pos = 0
        while pos < len(data):
            n = self.cmap.code_length(data, pos, default_len) if self.cmap else default_len
            code = data[pos:pos + n]
            pos += n
            if self.cmap and code in self.cmap.map:
                out.append(self.cmap.map[code])
            elif not self.composite:
                out.append(self.simple_map.get(code[0], "�"))
            else:
                out.append("�")
        return "".join(out)
