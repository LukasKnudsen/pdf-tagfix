import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pikepdf
import pytest

from pdf_tagfix.server import make_handler
from test_dump_tree import _make_pdf


@pytest.fixture
def server(tmp_path):
    _make_pdf(tmp_path / "tagged.pdf")
    plain = pikepdf.new()
    plain.add_blank_page()
    plain.save(tmp_path / "plain.pdf")
    (tmp_path / "notes.txt").write_text("not a pdf")

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(tmp_path, tmp_path / "no-ui"))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def _get(url):
    try:
        with urllib.request.urlopen(url) as res:
            return res.status, res.read()
    except urllib.error.HTTPError as err:
        return err.code, err.read()


def test_files(server):
    status, body = _get(server + "/api/files")
    assert status == 200
    assert json.loads(body)["files"] == ["plain.pdf", "tagged.pdf"]


def test_tree(server):
    status, body = _get(server + "/api/tree?file=tagged.pdf")
    assert status == 200
    data = json.loads(body)
    assert data["lang"] == "da"
    assert data["missing_mcids"] == 1
    h1 = data["root"]["kids"][0]["kids"][0]
    assert (h1["type"], h1["role"], h1["text"]) == ("Overskrift", "H1", "Overskrift")


def test_untagged_pdf(server):
    status, body = _get(server + "/api/tree?file=plain.pdf")
    assert status == 422
    assert "no tags" in json.loads(body)["error"]


def test_pdf_bytes(server):
    status, body = _get(server + "/api/pdf?file=tagged.pdf")
    assert status == 200
    assert body.startswith(b"%PDF")


@pytest.mark.parametrize("name", ["../secret.pdf", "notes.txt", "missing.pdf", ""])
def test_only_listed_pdfs_are_served(server, name):
    status, _ = _get(server + f"/api/pdf?file={name}")
    assert status == 404
