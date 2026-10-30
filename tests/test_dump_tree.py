import pikepdf
import pytest

from pdf_tagfix.dump_tree import main
from pdf_tagfix.structure import NoStructTree, StructNode, read_struct_tree

CONTENT = b"""
/H1 <</MCID 0>> BDC
BT /FB 12 Tf 2 0 0 2 72 700 Tm (Overskrift) Tj ET
EMC
/P <</MCID 1>> BDC
BT /FR 11 Tf 72 650 Td (Br\xf8dtekst med ) Tj ET
EMC
/Span <</MCID 2>> BDC
BT /FB 11 Tf 172 650 Td [(fed) -300 (del)] TJ ET
EMC
/P <</MCID 3>> BDC
BT /FR 9 Tf 72 600 Td (Ikke tagget) Tj ET
EMC
/Artifact BMC
BT /FR 8 Tf 72 30 Td (Side 1) Tj ET
EMC
"""


def _make_pdf(path):
    pdf = pikepdf.new()
    fonts = pikepdf.Dictionary(
        FR=pdf.make_indirect(pikepdf.Dictionary(
            Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1,
            BaseFont=pikepdf.Name.Helvetica, Encoding=pikepdf.Name.WinAnsiEncoding)),
        FB=pdf.make_indirect(pikepdf.Dictionary(
            Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1,
            BaseFont=pikepdf.Name("/Helvetica-Bold"), Encoding=pikepdf.Name.WinAnsiEncoding)),
    )
    page = pdf.add_blank_page(page_size=(612, 792))
    page.obj.Resources = pikepdf.Dictionary(Font=fonts)
    page.obj.Contents = pdf.make_stream(CONTENT)
    pg = page.obj

    def elem(s, k):
        return pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.StructElem,
                                                    S=pikepdf.Name(s), Pg=pg, K=k))

    h1 = elem("/Overskrift", 0)
    span = elem("/Span", 2)
    p = elem("/P", pikepdf.Array([1, span]))
    empty = elem("/P", 7)  # points to an MCID that does not exist
    doc = elem("/Document", pikepdf.Array([h1, p, empty]))
    pdf.Root.StructTreeRoot = pdf.make_indirect(pikepdf.Dictionary(
        Type=pikepdf.Name.StructTreeRoot, K=doc,
        RoleMap=pikepdf.Dictionary(Overskrift=pikepdf.Name.H1)))
    pdf.Root.MarkInfo = pikepdf.Dictionary(Marked=True)
    pdf.Root.Lang = pikepdf.String("da")
    pdf.save(path)


@pytest.fixture
def tagged_pdf(tmp_path):
    path = tmp_path / "tagged.pdf"
    _make_pdf(path)
    return path


def _find(node, type_):
    if isinstance(node, StructNode):
        if node.type == type_:
            return node
        for kid in node.kids:
            if (hit := _find(kid, type_)) is not None:
                return hit
    return None


def test_struct_tree(tagged_pdf):
    with pikepdf.open(tagged_pdf) as pdf:
        tree = read_struct_tree(pdf)

    assert tree.lang == "da"
    h1 = _find(tree.root, "Overskrift")
    assert h1.role == "H1"
    assert h1.text == "Overskrift"
    assert list(h1.font_sizes()) == [24]  # 12pt scaled 2x by Tm
    assert h1.bold_state() == "fed"

    p = _find(tree.root, "P")
    assert p.text == "Brødtekst med"
    assert p.bold_state() == "ikke-fed"
    assert list(p.font_sizes()) == [11]

    span = _find(tree.root, "Span")
    assert span.text == "fed del"  # TJ gap of -300 becomes a space
    assert span.bold_state() == "fed"

    assert [r.mcid for r in tree.missing] == [7]
    assert [k[2] for k in tree.unreferenced] == [3]


def test_cli_output(tagged_pdf, capsys):
    assert main([str(tagged_pdf)]) == 0
    out = capsys.readouterr().out
    assert 'Overskrift → H1  s.1  "Overskrift"  [24pt, fed]' in out
    assert '"Brødtekst med"  [11pt, ikke-fed]' in out
    assert "MCID 7 (s.1) findes ikke i indholdet" in out


def test_untagged_pdf(tmp_path):
    path = tmp_path / "plain.pdf"
    pdf = pikepdf.new()
    pdf.add_blank_page()
    pdf.save(path)
    with pikepdf.open(path) as pdf, pytest.raises(NoStructTree):
        read_struct_tree(pdf)
    assert main([str(path)]) == 2
