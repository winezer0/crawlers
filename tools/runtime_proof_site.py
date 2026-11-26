"""Deterministic runtime-only HTTP oracle; invalid testfill never counts as success."""
import hashlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
from urllib.parse import parse_qs, urlsplit

FIXTURE = Path(__file__).parent / "local_sites/runtime-proof"
ACTIONS = ("click", "nested-menu", "page-2", "scroll-event", "scroll-bottom", "response-chain", "delayed")
VARIANTS = {"alpha": "actions", "beta": "operations"}


def token(variant):
    return hashlib.sha256(("runtime-proof-v1|" + variant).encode()).hexdigest()[:20]


class Site(ThreadingHTTPServer):
    def __init__(self, port=18891):
        super().__init__(("127.0.0.1", port), Handler)
        self.events = []
        self.lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def respond(self, status, body, content_type="application/json", request_body=""):
        data = body.encode()
        with self.server.lock:
            self.server.events.append({"method": self.command, "path": self.path, "status": status,
                                       "headers": dict(self.headers), "request_body": request_body,
                                       "response_body": body})
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urlsplit(self.path)
        variant = parsed.path.strip("/")
        if variant in VARIANTS:
            self.respond(200, f'''<!doctype html><html><body data-variant="{variant}">
<h1>Runtime proof {variant}</h1><button id="click" disabled>Click</button>
<button id="menu">Open menu</button><button id="page">Next page</button>
<div style="height:1800px"></div><script src="/app.js"></script></body></html>''', "text/html")
        elif parsed.path == "/app.js":
            self.respond(200, (FIXTURE / "app.js").read_text(), "application/javascript")
        elif parsed.path == "/state":
            variant = parse_qs(parsed.query).get("variant", [""])[0]
            if variant in VARIANTS:
                self.respond(200, json.dumps({"collection": VARIANTS[variant], "token": token(variant)}))
            else:
                self.respond(400, '{}')
        else:
            self.respond(404, '{}')

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length > 65536:
            self.respond(413, '{}')
            return
        raw = self.rfile.read(length).decode(errors="replace")
        try:
            body = json.loads(raw)
        except ValueError:
            body = {}
        parts = urlsplit(self.path).path.strip("/").split("/")
        valid = False
        if len(parts) == 3 and parts[2] in ACTIONS and isinstance(body, dict):
            variant = body.get("variant")
            valid = variant in VARIANTS and parts[:2] == [VARIANTS[variant], token(variant)] \
                and self.headers.get("X-Runtime-Token") == token(variant) \
                and body == {"action": parts[2], "variant": variant, "proof": token(variant)}
        self.respond(200 if valid else 422, json.dumps({"accepted": valid}), request_body=raw)
