"""Local web UI: serves the tag tree of the PDFs in a folder as JSON, plus the built UI.

    pdf-tagfix-ui [FOLDER] [--port 8000]

Uses only the standard library. Binds to 127.0.0.1, so it is not reachable from
other machines.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pikepdf

from .dump_tree import tree_to_json
from .structure import NoStructTree, read_struct_tree

UI_DIST = Path(__file__).resolve().parents[2] / "ui" / "dist"


class ApiError(Exception):
    def __init__(self, status: HTTPStatus, message: str):
        super().__init__(message)
        self.status = status


def list_pdfs(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".pdf")


def resolve_pdf(folder: Path, name: str | None) -> Path:
    """Only files listed in the folder are served, so `name` can't escape it."""
    if not name or name not in list_pdfs(folder):
        raise ApiError(HTTPStatus.NOT_FOUND, f"no such PDF: {name}")
    return folder / name


def load_tree(path: Path) -> dict:
    try:
        with pikepdf.open(path) as pdf:
            return tree_to_json(read_struct_tree(pdf), path.name, len(pdf.pages))
    except NoStructTree as exc:
        raise ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, str(exc)) from exc
    except pikepdf.PdfError as exc:
        raise ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, f"cannot open: {exc}") from exc


def make_handler(folder: Path, ui_dist: Path = UI_DIST):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            url = urlparse(self.path)
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            try:
                if url.path == "/api/files":
                    self._json({"folder": str(folder), "files": list_pdfs(folder)})
                elif url.path == "/api/tree":
                    self._json(load_tree(resolve_pdf(folder, query.get("file"))))
                elif url.path == "/api/pdf":
                    self._send(resolve_pdf(folder, query.get("file")).read_bytes(),
                               "application/pdf")
                elif url.path.startswith("/api/"):
                    raise ApiError(HTTPStatus.NOT_FOUND, "unknown endpoint")
                else:
                    self._static(url.path)
            except ApiError as exc:
                self._json({"error": str(exc)}, exc.status)

        def _static(self, path: str):
            if not ui_dist.is_dir():
                self._send(b"The UI is not built. Run `npm run build` in ui/, "
                           b"or use `npm run dev` there during development.\n",
                           "text/plain; charset=utf-8", HTTPStatus.NOT_FOUND)
                return
            target = (ui_dist / path.lstrip("/")).resolve()
            if not target.is_relative_to(ui_dist.resolve()) or not target.is_file():
                target = ui_dist / "index.html"
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            self._send(target.read_bytes(), ctype)

        def _json(self, data, status=HTTPStatus.OK):
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self._send(body, "application/json; charset=utf-8", status)

        def _send(self, body: bytes, ctype: str, status=HTTPStatus.OK):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            pass

    return Handler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Browse the tag trees of the PDFs in a folder.")
    parser.add_argument("folder", nargs="?", default="samples",
                        help="folder with PDFs (default: samples)")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)

    folder = Path(args.folder).resolve()
    if not folder.is_dir():
        parser.error(f"not a folder: {folder}")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(folder))
    print(f"Serving PDFs from {folder}")
    print(f"Open http://127.0.0.1:{args.port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
