"""Check installed Katana option compatibility on a single local HTML page."""
import argparse
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from katana_enhanced_baseline import KATANA, discovery_flags, save


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"<html><body><h1>Katana option compatibility</h1></body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    # Each omission diagnoses compatibility, never substitutes a weaker reference.
    variants = (("diagnostics-enabled", ["-ed"], set()),
                ("diagnostics-disabled", [], set()),
                ("default-content-filter", [], {"-duf"}),
                ("without-path-climb", [], {"-pc"}))
    try:
        for name, added, removed in variants:
            output = args.out_dir / (name + ".jsonl")
            if output.exists():
                raise RuntimeError("Use a new output directory")
            flags = [flag for flag in discovery_flags("headless") if flag not in removed] + added
            command = [str(KATANA), "-u", f"http://127.0.0.1:{server.server_port}/", *flags,
                       "-sc", "-scp", "C:/Program Files/Google/Chrome/Application/chrome.exe", "-o", str(output.resolve()),
                       "-srd", str((args.out_dir / name / "responses").resolve())]
            try:
                completed = subprocess.run(command, capture_output=True, timeout=45)
                result = {"command": command, "exit_code": completed.returncode,
                          "stdout_tail": completed.stdout.decode(errors="replace")[-1000:],
                          "stderr": completed.stderr.decode(errors="replace"),
                          "output_bytes": output.stat().st_size if output.exists() else 0}
            except subprocess.TimeoutExpired:
                result = {"command": command, "timeout": True}
            save(args.out_dir / (name + ".json"), result)
            print(name, result.get("output_bytes"), flush=True)
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
