"""Build an evidence-backed frontend dependency dataset and replay it offline.

Network is disabled unless --download is explicitly supplied. Captured requests
are evidence, never sent by this tool. Only same-origin JavaScript dependencies
from script tags, bootstrap contracts, or reached module-load calls are fetched.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]


def digest(body):
    return hashlib.sha256(body).hexdigest()


def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def origin(url):
    p = urlsplit(url)
    if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password:
        raise ValueError("Expected an HTTP URL without credentials")
    return p.scheme, p.hostname.lower(), p.port or (443 if p.scheme == "https" else 80)


def scoped_url(value, base, document):
    url = urljoin(base, value)
    p = urlsplit(url)
    if origin(url) != origin(document):
        raise ValueError("Dependency is outside document origin")
    return urlunsplit((p.scheme, p.netloc, p.path, p.query, ""))


class Frontend(HTMLParser):
    def __init__(self, document):
        super().__init__()
        self.document = self.base = document
        self.base_seen = False
        self.sources, self.inline = [], []
        self.active = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "base" and not self.base_seen and attrs.get("href"):
            self.base = urljoin(self.document, attrs["href"])
            self.base_seen = True
        if tag == "script":
            if attrs.get("src"):
                try:
                    self.sources.append(scoped_url(attrs["src"], self.base, self.document))
                except ValueError:
                    pass
            kind = attrs.get("type", "").lower()
            self.active = [] if "src" not in attrs and kind in ("", "text/javascript", "application/javascript") else None

    def handle_data(self, data):
        if self.active is not None:
            self.active.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self.active is not None:
            self.inline.append("".join(self.active))
            self.active = None


def frontend_evidence(body, document):
    parsed = Frontend(document)
    parsed.feed(body)
    entries = [s for s in parsed.inline if "TURBOPACK" in s and "runtimeModuleIds" in s]
    if not entries:
        raise ValueError("No observed inline Turbopack bootstrap")
    scripts = [s for s in parsed.inline if s in entries or ("__next_f" in s and ".push" in s)]
    evidence = {u: [{"kind": "html-script-src", "source": document}] for u in parsed.sources}
    paths = []
    for script in entries:
        try:
            value, _ = json.JSONDecoder().raw_decode(script.split(".push(", 1)[1].lstrip())
        except (ValueError, IndexError):
            continue  # Executable bootstraps are handled by the VM, not guessed.
        params = value if isinstance(value, dict) else next((v for v in value if isinstance(v, dict) and "runtimeModuleIds" in v), {}) if isinstance(value, list) else {}
        for chunk in params.get("otherChunks", []):
            path = chunk if isinstance(chunk, str) else chunk.get("path") if isinstance(chunk, dict) else None
            if path:
                paths.append(path)
    prefixes = {urlsplit(src).path[:-len(path)] for src in parsed.sources for path in paths if urlsplit(src).path.endswith("/" + path)}
    for path in paths:
        if path.startswith(("/", "./", "../", "http://", "https://")):
            target = path
        elif len(prefixes) == 1:
            target = next(iter(prefixes)) + path
        else:
            continue  # No proven deployment base: do not invent one.
        try:
            url = scoped_url(target, parsed.base, document)
            evidence.setdefault(url, []).append({"kind": "bootstrap-otherChunks", "source": document, "path": path})
        except ValueError:
            pass
    return "\n;\n".join(scripts).encode("utf-8"), evidence, len(scripts)


class SameOriginRedirect(HTTPRedirectHandler):
    def __init__(self, document):
        self.document = document

    def redirect_request(self, request, fp, code, msg, headers, newurl):
        scoped_url(newurl, request.full_url, self.document)
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def collect(args, state, evidence):
    requests = 0
    opener = build_opener(SameOriginRedirect(args.document_url))
    for url, sources in sorted(evidence.items()):
        record = state["modules"].setdefault(url, {"evidence": []})
        for source in sources:
            if source not in record["evidence"]:
                record["evidence"].append(source)
        if record.get("file"):
            body = (args.dataset / record["file"]).read_bytes()
            if digest(body) != record["sha256"]:
                raise ValueError("Cached source hash changed: " + url)
            continue
        if not args.download or record.get("failure"):
            continue  # Failed evidence is retained; no automatic retry loop.
        if args.download_count >= args.max_downloads:
            continue
        args.download_count += 1
        requests += 1
        started = time.monotonic()
        try:
            with opener.open(Request(url, headers={"Accept": "application/javascript"}), timeout=20) as response:
                body = response.read(args.max_file_bytes + 1)
                mime = response.headers.get("Content-Type", "")
                record.update(http_status=response.status, content_type=mime, final_url=response.url)
            if len(body) > args.max_file_bytes:
                raise ValueError("JavaScript response exceeds byte limit")
            if response.status != 200 or not any(m in mime.lower() for m in ("javascript", "ecmascript")):
                raise ValueError("Response is not successful JavaScript")
            total = sum(m.get("bytes", 0) for m in state["modules"].values())
            if total + len(body) > args.max_total_bytes:
                raise ValueError("Dataset exceeds total byte limit")
            relative = "sources/" + digest(url.encode()) + ".js"
            (args.dataset / relative).write_bytes(body)
            record.update(file=relative, bytes=len(body), sha256=digest(body))
        except (HTTPError, OSError, ValueError) as error:
            record["failure"] = {"type": type(error).__name__, "message": str(error)}
            if isinstance(error, HTTPError):
                record["http_status"] = error.code
        record["download_seconds"] = round(time.monotonic() - started, 6)
        print(json.dumps({"dependency": url, "status": record.get("http_status"), "cached": bool(record.get("file")), "failure": record.get("failure")}), flush=True)
        save(args.dataset / "dataset.json", state)
    return requests


def classify_unavailable(state):
    return [{"url": u, "state": "download_failed" if r.get("failure") else "not_downloaded", "failure": r.get("failure")} for u, r in sorted(state["modules"].items()) if not r.get("file")]


def verified_modules(dataset, state):
    modules = {}
    for url, record in state["modules"].items():
        if not record.get("file"):
            continue
        path = (dataset / record["file"]).resolve()
        if not path.is_relative_to(dataset.resolve()):
            raise ValueError("Cached source escapes dataset: " + url)
        if digest(path.read_bytes()) != record["sha256"]:
            raise ValueError("Cached source hash changed: " + url)
        modules[url] = str(path)
    return modules


def runtime_fingerprint():
    sources = hashlib.sha256()
    for file in sorted((ROOT / "basecrawler/internal/routeexec").glob("*.go")):
        sources.update(file.name.encode() + file.read_bytes())
    return sources.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--html", type=Path, required=True)
    parser.add_argument("--document-url", required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--seed-manifest", type=Path)
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--with-dom", action="store_true", help="Install the actual saved HTML tree; does not render or trigger links")
    parser.add_argument("--max-downloads", type=int, default=96)
    parser.add_argument("--max-rounds", type=int, default=32)
    parser.add_argument("--max-file-bytes", type=int, default=4 << 20)
    parser.add_argument("--max-total-bytes", type=int, default=32 << 20)
    args = parser.parse_args()
    if min(args.max_downloads, args.max_rounds, args.max_file_bytes, args.max_total_bytes) <= 0:
        parser.error("Limits must be positive")
    started = time.monotonic()
    args.dataset = args.dataset.resolve()
    args.dataset.mkdir(parents=True, exist_ok=True)
    (args.dataset / "sources").mkdir(exist_ok=True)
    frontend = args.html.read_bytes()
    bootstrap, evidence, scripts = frontend_evidence(frontend.decode("utf-8"), args.document_url)
    state_file = args.dataset / "dataset.json"
    state = json.loads(state_file.read_text(encoding="utf-8")) if state_file.exists() else {"schema": 1, "modules": {}}
    if state.get("document_url", args.document_url) != args.document_url or state.get("frontend_sha256", digest(frontend)) != digest(frontend):
        parser.error("Dataset belongs to a different document snapshot; use a new directory")
    state.update(document_url=args.document_url, frontend_sha256=digest(frontend), bootstrap_sha256=digest(bootstrap), inline_scripts=scripts)
    (args.dataset / "frontend.html").write_bytes(frontend)
    (args.dataset / "bootstrap.js").write_bytes(bootstrap)
    if args.seed_manifest:
        for url, file in json.loads(args.seed_manifest.read_text(encoding="utf-8")).items():
            url = scoped_url(url, args.document_url, args.document_url)
            body = Path(file).read_bytes()
            if len(body) > args.max_file_bytes:
                raise ValueError("Seed file exceeds byte limit")
            total = sum(r.get("bytes", 0) for key, r in state["modules"].items() if key != url)
            if total + len(body) > args.max_total_bytes:
                raise ValueError("Seed dataset exceeds total byte limit")
            relative = "sources/" + digest(url.encode()) + ".js"
            (args.dataset / relative).write_bytes(body)
            state["modules"][url] = {"file": relative, "sha256": digest(body), "bytes": len(body), "evidence": [{"kind": "imported-cache", "source": str(args.seed_manifest.resolve())}]}
    args.download_count = 0
    verified_modules(args.dataset, state)
    rounds, network = [], 0
    go = shutil.which("go")
    if not go:
        parser.error("Go is required for execution replay")
    runtime_hash = runtime_fingerprint()
    stop_reason = "round_limit"
    with tempfile.TemporaryDirectory(prefix="crawler-runtime-") as temporary:
        executable = Path(temporary) / ("runtime.exe" if os.name == "nt" else "runtime")
        subprocess.run([go, "test", "-c", "-o", str(executable), "./internal/routeexec"], cwd=ROOT / "basecrawler", check=True, capture_output=True, timeout=120)
        if runtime_hash != runtime_fingerprint():
            raise RuntimeError("Runtime source changed during build; repeat replay")
        executable_hash = digest(executable.read_bytes())
        for index in range(args.max_rounds):
            network += collect(args, state, evidence)
            save(state_file, state)
            modules = verified_modules(args.dataset, state)
            save(args.dataset / "modules.json", modules)
            report_file = args.dataset / f"round-{index + 1:03d}.json"
            report_file.unlink(missing_ok=True)
            env = dict(os.environ)
            env["ROUTE_EXEC_DOCUMENT_HTML"] = str(args.dataset / "frontend.html") if args.with_dom else ""
            env.update(ROUTE_EXEC_FIXTURE=str(args.dataset / "bootstrap.js"), ROUTE_EXEC_MODULE_MANIFEST=str(args.dataset / "modules.json"), ROUTE_EXEC_SOURCE_URL=args.document_url + "#basecrawler-turbopack-bootstrap", ROUTE_EXEC_DOCUMENT_URL=args.document_url, ROUTE_EXEC_REPORT=str(report_file), ROUTE_EXEC_MAX_DEPENDENCIES="512", ROUTE_EXEC_MAX_DEPENDENCY_BYTES=str(args.max_total_bytes))
            run = subprocess.run([str(executable), "-test.run", "^TestDownloadedRuntimeFixture$", "-test.v"], env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
            report_file.with_suffix(".log").write_text(run.stdout + run.stderr, encoding="utf-8")
            if not report_file.exists():
                raise RuntimeError("Runtime did not write diagnostic JSON: " + run.stdout + run.stderr)
            report = json.loads(report_file.read_text(encoding="utf-8"))
            report["test_exit_code"] = run.returncode
            report.update(with_dom=args.with_dom, runtime_source_sha256=runtime_hash, runtime_executable_sha256=executable_hash)
            save(report_file, report)
            rounds.append(report)
            print(json.dumps({"round": index + 1, "status": report["status"], "requests": len(report.get("requests") or []), "error": report["error"][:700]}), flush=True)
            pending = {}
            for dep in report["dependencies"]:
                url = scoped_url(dep["url"], args.document_url, args.document_url)
                pending.setdefault(url, []).append({"kind": "executed-module-load", "source": dep["parent"], "round": index + 1})
            for url, sources in pending.items():
                record = state["modules"].setdefault(url, {"evidence": []})
                record["evidence"].extend(s for s in sources if s not in record["evidence"])
            save(state_file, state)
            downloadable = any(not state["modules"][u].get("file") and not state["modules"][u].get("failure") for u in pending)
            if report["status"] != "blocked_dependency" or not args.download or not downloadable or args.download_count >= args.max_downloads:
                stop_reason = "runtime_result" if report["status"] != "blocked_dependency" else "offline" if not args.download else "download_limit" if args.download_count >= args.max_downloads else "dependency_unavailable"
                break
            evidence = pending
    summary = {"status": rounds[-1]["status"], "coverage_accepted": False, "rounds": len(rounds), "modules": len([r for r in state["modules"].values() if r.get("file")]), "network_requests": network, "downloads_enabled": args.download, "unavailable": classify_unavailable(state), "runtime_source_sha256": runtime_hash, "runtime_executable_sha256": executable_hash, "source_changed_during_replay": runtime_hash != runtime_fingerprint(), "stop_reason": stop_reason, "environment_gaps": rounds[-1].get("environment_gaps", []), "dependency_limits": {"modules": 512, "bytes": args.max_total_bytes}, "wall_seconds": round(time.monotonic() - started, 6), "execution_seconds": sum(r["elapsed_seconds"] for r in rounds), "last_error": rounds[-1]["error"], "captured_requests": rounds[-1].get("requests") or [], "policy": "VM does not send requests. Initialization or capture is not full crawler acceptance."}
    summary.update(with_dom=args.with_dom, runtime_state=rounds[-1].get("runtime_state", {}), runtime_diagnostics=rounds[-1].get("runtime_diagnostics", []), pending_tasks=rounds[-1].get("pending_tasks", []))
    save(args.dataset / "summary.json", summary)
    with (args.dataset / "replay-history.jsonl").open("a", encoding="utf-8") as history:
        history.write(json.dumps(summary, ensure_ascii=False) + "\n")
    print(json.dumps({k: summary[k] for k in ("status", "rounds", "modules", "network_requests", "wall_seconds")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
