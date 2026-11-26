"""Read-only preview of the real frontend, with an optional editorial stylesheet.

No production API is exposed. Demo data is provided in the browser by demo.js.
"""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit
import argparse

HERE = Path(__file__).resolve().parent
FRONTEND = HERE.parents[1] / "frontend"


class PreviewHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if urlsplit(self.path).path in ("/", "/index.html"):
            page = (FRONTEND / "index.html").read_text()
            page = page.replace("<title>AI Interview</title>", "<title>AI Interview · 風格預覽</title>")
            page = page.replace(
                '<script src="/js/theme.js"></script>',
                '<link rel="stylesheet" href="/preview/preview.css">'
                '<link id="editorial-style" rel="stylesheet" href="/preview/editorial.css">'
                '<script src="/preview/demo.js"></script>'
                '<script src="/js/theme.js"></script>',
            )
            content = page.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(content)
            return
        if urlsplit(self.path).path.startswith("/api/"):
            self.send_error(404, "Preview only; no production API")
            return
        super().do_GET()

    def translate_path(self, path):
        name = unquote(urlsplit(path).path)
        for prefix, base in (("/js/", FRONTEND / "js"), ("/css/", FRONTEND / "css"), ("/preview/", HERE)):
            if name.startswith(prefix):
                candidate = (base / name[len(prefix):]).resolve()
                if candidate.is_relative_to(base.resolve()) and candidate.is_file():
                    return str(candidate)
        return str(HERE / "__not_found__")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8012)
    args = parser.parse_args()
    with ThreadingHTTPServer(("127.0.0.1", args.port), partial(PreviewHandler, directory=str(HERE))) as server:
        print(f"AI Interview style preview: http://127.0.0.1:{args.port}/#new", flush=True)
        server.serve_forever()
