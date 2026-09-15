"""Localhost HTTP adapter around Relay (scratch only)."""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .relay import Relay
from .store import Store


def make_handler(relay: Relay):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args) -> None:  # noqa: A003
            return

        def _headers(self) -> dict[str, str]:
            return {k: v for k, v in self.headers.items()}

        def _read_body(self) -> bytes:
            n = int(self.headers.get("Content-Length") or "0")
            if n <= 0:
                return b""
            return self.rfile.read(n)

        def _respond(self, status: int, headers: dict[str, str], body: bytes) -> None:
            self.send_response(status)
            for k, v in headers.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            query = {k: v[-1] for k, v in parse_qs(parsed.query).items()}
            status, headers, body = relay.handle(
                "GET", parsed.path, headers=self._headers(), body=b"", query=query
            )
            self._respond(status, headers, body)

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            status, headers, body = relay.handle(
                "POST", parsed.path, headers=self._headers(), body=self._read_body()
            )
            self._respond(status, headers, body)

    return Handler


def serve(host: str, port: int, db_path: str) -> None:
    store = Store(db_path)
    relay = Relay(store)
    ThreadingHTTPServer.allow_reuse_address = True
    httpd = ThreadingHTTPServer((host, port), make_handler(relay))
    print(json.dumps({"listening": f"http://{host}:{port}", "db": db_path}))
    httpd.serve_forever()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--db", required=True)
    args = ap.parse_args()
    serve(args.host, args.port, args.db)


if __name__ == "__main__":
    main()
