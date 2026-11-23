"""Build an evidence-backed frontend dependency dataset and replay it offline.

Network is disabled unless --download is explicitly supplied. Captured requests
are evidence, never sent by this tool. Only observed same-origin JavaScript and
stylesheet dependencies are fetched. CSS resources never execute as JavaScript.
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
from urllib.parse import parse_qsl, urljoin, urlsplit, urlunsplit
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
        self.sources, self.inline, self.stylesheets = [], [], []
        self.links = []
        self.active = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ("a", "area") and attrs.get("href"):
            try:
                self.links.append(scoped_url(attrs["href"], self.base, self.document))
            except ValueError:
                pass
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
        if tag == "link" and "stylesheet" in attrs.get("rel", "").lower().split() and attrs.get("href") and "disabled" not in attrs:
            try:
                self.stylesheets.append(scoped_url(attrs["href"], self.base, self.document))
            except ValueError:
                pass

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


def collect(args, state, evidence, kind="javascript"):
    requests = 0
    records = state.setdefault("stylesheets", {}) if kind == "css" else state["modules"]
    opener = build_opener(SameOriginRedirect(args.document_url))
    for url, sources in sorted(evidence.items()):
        record = records.setdefault(url, {"evidence": []})
        for source in sources:
            if source not in record["evidence"]:
                record["evidence"].append(source)
        if record.get("file"):
            path = (args.dataset / record["file"]).resolve()
            if not path.is_relative_to(args.dataset.resolve()):
                raise ValueError("Cached source escapes dataset: " + url)
            body = path.read_bytes()
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
            with opener.open(Request(url, headers={"Accept": "text/css" if kind == "css" else "application/javascript"}), timeout=20) as response:
                body = response.read(args.max_file_bytes + 1)
                mime = response.headers.get("Content-Type", "")
                record.update(http_status=response.status, content_type=mime, final_url=response.url)
            if len(body) > args.max_file_bytes:
                raise ValueError("Dependency response exceeds byte limit")
            valid_mime = mime.split(";", 1)[0].strip().lower() == "text/css" if kind == "css" else any(m in mime.lower() for m in ("javascript", "ecmascript"))
            if response.status != 200 or not valid_mime:
                raise ValueError("Response is not successful " + kind)
            total = sum(m.get("bytes", 0) for group in ("modules", "stylesheets") for m in state.get(group, {}).values())
            if total + len(body) > args.max_total_bytes:
                raise ValueError("Dataset exceeds total byte limit")
            relative = "sources/" + digest(url.encode()) + (".css" if kind == "css" else ".js")
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


def audit_frozen_missing(reference, captured, links=(), runtime_error=""):
    """URL-level call evidence only; related RSC variants never close a target."""
    def resource(url):
        parts = urlsplit(url)
        return origin(url), parts.path or "/", tuple(sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "_rsc"))

    calls = [r for r in captured if r.get("Kind") != "navigation" and r.get("Evidence") == "executed-request-call"]
    rows = []
    for url in reference["missing"]:
        exact = [r for r in calls if r.get("Method", "GET") == "GET" and r["Raw"] == url]
        related = [r for r in calls if r.get("Method", "GET") == "GET" and resource(r["Raw"]) == resource(url) and r["Raw"] != url]
        link_targets = sorted({link for link in links if resource(link) == resource(url)})
        rows.append({"method": "GET", "reference_url": url, "exact_url_call": bool(exact),
                     "link_target_observed": bool(link_targets), "link_targets": link_targets,
                     "request_variant_observed": bool(exact),
                     "remaining_reason": "HTTP response not verified" if exact else "Related request uses a different context; equivalence unproven" if related else "Link exists; request callback not reached" if link_targets else "Neither link nor request observed in this saved document",
                     "runtime_blocker": runtime_error[:2048],
                     "related_variants": [{"url": r["Raw"], "headers": r.get("Headers", {})} for r in related],
                     "state": "exact_url_call_not_sent" if exact else "different_variant_not_closed" if related else "not_observed",
                     "queued": False, "sent": False, "response_confirmed": False, "coverage_accepted": False})
    return {"targets": len(rows), "exact_url_calls": sum(r["exact_url_call"] for r in rows),
            "link_target_observed": sum(r["link_target_observed"] for r in rows),
            "related_variant_targets": sum(bool(r["related_variants"]) for r in rows), "closed_targets": 0,
            "network_requests": 0, "rows": rows,
            "policy": "Offline call evidence only. Navigation is excluded. Different RSC hashes or contexts do not prove equivalent responses."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--html", type=Path, required=True)
    parser.add_argument("--document-url", required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--seed-manifest", type=Path)
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--with-dom", action="store_true", help="Install the actual saved HTML tree; does not render or trigger links")
    parser.add_argument("--media-environment", type=Path, help="JSON declaring virtual media inputs, not measured browser/screen data")
    parser.add_argument("--frozen-effective", type=Path, help="Saved effective.json with missing GET targets; audit only, never rewrite the reference")
    parser.add_argument("--reference-entry", help="Frozen benchmark entry URL; labels different-page diagnostics explicitly")
    parser.add_argument("--max-downloads", type=int, default=96)

    def positive_seconds(value):
        seconds = int(value)
        if seconds <= 0:
            raise argparse.ArgumentTypeError("execution timeout must be positive")
        return seconds

    parser.add_argument("--exec-timeout", type=positive_seconds, default=30, help="Offline diagnostic VM timeout in seconds; production defaults unchanged")
    parser.add_argument("--max-rounds", type=int, default=32)
    parser.add_argument("--max-file-bytes", type=int, default=4 << 20)
    parser.add_argument("--max-total-bytes", type=int, default=32 << 20)
    args = parser.parse_args()
    if min(args.max_downloads, args.max_rounds, args.max_file_bytes, args.max_total_bytes) <= 0:
        parser.error("Limits must be positive")
    started = time.monotonic()
    args.dataset = args.dataset.resolve()
    args.dataset.mkdir(parents=True, exist_ok=True)
    media_environment = None
    if args.media_environment:
        media_environment = json.loads(args.media_environment.read_text(encoding="utf-8"))
        if not isinstance(media_environment, dict):
            parser.error("Media environment must be a JSON object")
    media_json = json.dumps(media_environment, sort_keys=True) if media_environment is not None else ""
    report_directory = args.dataset
    report_directory.mkdir(parents=True, exist_ok=True)
    (args.dataset / "sources").mkdir(exist_ok=True)
    frontend = args.html.read_bytes()
    bootstrap, evidence, scripts = frontend_evidence(frontend.decode("utf-8"), args.document_url)
    parsed = Frontend(args.document_url)
    parsed.feed(frontend.decode("utf-8"))
    style_evidence = {u: [{"kind": "html-stylesheet-link", "source": args.document_url}] for u in parsed.stylesheets}
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
            network += collect(args, state, style_evidence, "css")
            save(state_file, state)
            modules = verified_modules(args.dataset, state)
            save(args.dataset / "modules.json", modules)
            stylesheets = verified_modules(args.dataset, {"modules": state.get("stylesheets", {})})
            save(args.dataset / "stylesheets.json", stylesheets)
            report_file = report_directory / f"round-{index + 1:03d}.json"
            report_file.unlink(missing_ok=True)
            env = dict(os.environ)
            env["ROUTE_EXEC_DOCUMENT_HTML"] = str(args.dataset / "frontend.html") if args.with_dom else ""
            env["ROUTE_EXEC_MEDIA_ENVIRONMENT"] = media_json
            env["ROUTE_EXEC_STYLESHEET_MANIFEST"] = str(args.dataset / "stylesheets.json")
            env["ROUTE_EXEC_TIMEOUT"] = f"{args.exec_timeout}s"
            env.update(ROUTE_EXEC_FIXTURE=str(args.dataset / "bootstrap.js"), ROUTE_EXEC_MODULE_MANIFEST=str(args.dataset / "modules.json"), ROUTE_EXEC_SOURCE_URL=args.document_url + "#basecrawler-turbopack-bootstrap", ROUTE_EXEC_DOCUMENT_URL=args.document_url, ROUTE_EXEC_REPORT=str(report_file), ROUTE_EXEC_MAX_DEPENDENCIES="512", ROUTE_EXEC_MAX_DEPENDENCY_BYTES=str(args.max_total_bytes))
            run = subprocess.run([str(executable), "-test.run", "^TestDownloadedRuntimeFixture$", "-test.v"], env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=args.exec_timeout + 15)
            report_file.with_suffix(".log").write_text(run.stdout + run.stderr, encoding="utf-8")
            if not report_file.exists():
                raise RuntimeError("Runtime did not write diagnostic JSON: " + run.stdout + run.stderr)
            report = json.loads(report_file.read_text(encoding="utf-8"))
            report["test_exit_code"] = run.returncode
            report.update(with_dom=args.with_dom, runtime_source_sha256=runtime_hash, runtime_executable_sha256=executable_hash)
            save(report_file, report)
            rounds.append(report)
            print(json.dumps({"round": index + 1, "status": report["status"], "observed_targets": len(report.get("requests") or []), "captured_http_calls": report.get("captured_http_calls", 0), "captured_navigation_calls": report.get("captured_navigation_calls", 0), "error": report["error"][:700]}), flush=True)
            pending, pending_styles = {}, {}
            for dep in report["dependencies"]:
                url = scoped_url(dep["url"], args.document_url, args.document_url)
                target = pending_styles if dep.get("kind") == "css" else pending
                target.setdefault(url, []).append({"kind": "executed-stylesheet-load" if dep.get("kind") == "css" else "executed-module-load", "source": dep["parent"], "round": index + 1})
            for url, sources in pending.items():
                record = state["modules"].setdefault(url, {"evidence": []})
                record["evidence"].extend(s for s in sources if s not in record["evidence"])
            for url, sources in pending_styles.items():
                record = state.setdefault("stylesheets", {}).setdefault(url, {"evidence": []})
                record["evidence"].extend(s for s in sources if s not in record["evidence"])
                style_evidence.setdefault(url, []).extend(s for s in sources if s not in style_evidence.get(url, []))
            save(state_file, state)
            downloadable = any(not state["modules"][u].get("file") and not state["modules"][u].get("failure") for u in pending)
            downloadable = downloadable or any(not state["stylesheets"][u].get("file") and not state["stylesheets"][u].get("failure") for u in pending_styles)
            if report["status"] != "blocked_dependency" or not args.download or not downloadable or args.download_count >= args.max_downloads:
                stop_reason = "runtime_result" if report["status"] != "blocked_dependency" else "offline" if not args.download else "download_limit" if args.download_count >= args.max_downloads else "dependency_unavailable"
                break
            evidence = pending
    summary = {"status": rounds[-1]["status"], "coverage_accepted": False, "rounds": len(rounds), "modules": len([r for r in state["modules"].values() if r.get("file")]), "network_requests": network, "downloads_enabled": args.download, "unavailable": classify_unavailable(state), "runtime_source_sha256": runtime_hash, "runtime_executable_sha256": executable_hash, "source_changed_during_replay": runtime_hash != runtime_fingerprint(), "stop_reason": stop_reason, "environment_gaps": rounds[-1].get("environment_gaps", []), "dependency_limits": {"modules": 512, "bytes": args.max_total_bytes}, "wall_seconds": round(time.monotonic() - started, 6), "execution_seconds": sum(r["elapsed_seconds"] for r in rounds), "last_error": rounds[-1]["error"], "captured_requests": rounds[-1].get("requests") or [], "policy": "VM does not send requests. Initialization or capture is not full crawler acceptance."}
    summary.update(with_dom=args.with_dom, runtime_state=rounds[-1].get("runtime_state", {}), runtime_diagnostics=rounds[-1].get("runtime_diagnostics", []), pending_tasks=rounds[-1].get("pending_tasks", []))
    summary.update(execution_stop_reason=rounds[-1].get("execution_stop_reason"), captured_http_calls=rounds[-1].get("captured_http_calls", 0), captured_navigation_calls=rounds[-1].get("captured_navigation_calls", 0))
    save(args.dataset / "summary.json", summary)
    if args.frozen_effective:
        reference_body = args.frozen_effective.read_bytes()
        frontend = Frontend(args.document_url)
        frontend.feed(args.html.read_text(encoding="utf-8"))
        audit = audit_frozen_missing(json.loads(reference_body), summary["captured_requests"], frontend.links, summary["last_error"])
        audit.update(reference=str(args.frozen_effective.resolve()), reference_sha256=digest(reference_body),
                     runtime_source_sha256=runtime_hash, document_url=args.document_url,
                     reference_entry=args.reference_entry,
                     comparison_scope="matching_entry_document_replay" if args.reference_entry == args.document_url else "different_document_diagnostic" if args.reference_entry else "unverified_entry_document_diagnostic")
        save(args.dataset / "frozen-missing-audit.json", audit)
    with (args.dataset / "replay-history.jsonl").open("a", encoding="utf-8") as history:
        history.write(json.dumps(summary, ensure_ascii=False) + "\n")
    print(json.dumps({k: summary[k] for k in ("status", "rounds", "modules", "network_requests", "wall_seconds")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
