#!/usr/bin/env python3
"""Build and crawl the local Nuxt and React regression sites."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import unquote, urlsplit
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
SITES = ROOT / "tools" / "local_sites"
BASECRAWLER = ROOT / "basecrawler"
FALSE_CANDIDATE_FRAGMENTS = (
    "accessibility.signals.chatRequestSent",
    "editor.background",
    "workbench.hover.pointer",
    "a.action-label",
    "starting-nuxt-server",
    "<span>example</span>",
)
SITE_CONFIG: dict[str, dict[str, Any]] = {
    "local-nuxt": {
        "directory": SITES / "nuxt",
        "entry": "node_modules/nuxt/bin/nuxt.mjs",
        "preview_args": ("preview", "--host", "127.0.0.1"),
        "port": 18731,
        "seed_path": "/",
        "expected_paths": ("/", "/nuxt/rendering", "/nuxt/client", "/api/nuxt-catalog"),
        "expected_interfaces": {"/api/nuxt-search": "POST"},
        "runtime_only_paths": ("/api/nuxt-click", "/api/nuxt-menu", "/api/nuxt-scroll", "/api/nuxt-page"),
        "source_map_prefixes": ("/_nuxt/",),
        "decoy_fixture": SITES / "nuxt" / "app" / "utils" / "nuxtCandidateDecoys.ts",
        "expected_decoy_count": 835,
    },
    "local-react": {
        "directory": SITES / "react",
        "entry": "node_modules/vite/bin/vite.js",
        "preview_args": ("preview", "--host", "127.0.0.1"),
        "port": 18732,
        "strict_port": True,
        "seed_path": "/",
        "expected_paths": ("/", "/react/guide", "/api/react-catalog"),
        "expected_interfaces": {"/api/react-search": "POST"},
        "runtime_only_paths": ("/api/react-click", "/api/react-menu", "/api/react-scroll", "/api/react-page"),
        "source_map_prefixes": ("/assets/",),
    },
    "local-vue": {
        "directory": SITES / "vue",
        "entry": "node_modules/vite/bin/vite.js",
        "preview_args": ("preview", "--host", "127.0.0.1"),
        "port": 18733,
        "strict_port": True,
        "seed_path": "/",
        "expected_paths": ("/", "/vue/guide", "/api/vue-catalog"),
        "expected_interfaces": {"/api/vue-search": "POST"},
        "runtime_only_paths": ("/api/vue-click", "/api/vue-menu", "/api/vue-scroll", "/api/vue-page"),
        "source_map_prefixes": ("/assets/",),
    },
    "local-next": {
        "directory": SITES / "next",
        "entry": "node_modules/next/dist/bin/next",
        "preview_args": ("start", "--hostname", "127.0.0.1"),
        "port": 18734,
        "seed_path": "/",
        "expected_paths": ("/", "/next/guide", "/api/next-catalog"),
        "expected_interfaces": {"/api/next-search": "POST"},
        "runtime_only_paths": ("/api/next-click", "/api/next-menu", "/api/next-scroll", "/api/next-page"),
        "source_map_prefixes": ("/_next/",),
    },
    "local-angular": {
        "directory": SITES / "angular",
        "entry": "serve.mjs",
        "build_entry": "node_modules/@angular/cli/bin/ng.js",
        "preview_args": (),
        "port": 18735,
        "fixture_name": "angular",
        "static_directory": "dist/local-angular/browser",
        "seed_path": "/",
        "expected_paths": ("/", "/angular/guide", "/api/angular-catalog"),
        "expected_interfaces": {"/api/angular-search": "POST"},
        "runtime_only_paths": (
            "/api/angular-click", "/api/angular-menu", "/api/angular-scroll", "/api/angular-page",
        ),
        "source_map_prefixes": ("/main-", "/polyfills-", "/chunk-", "/styles-"),
    },
    "local-sveltekit": {
        "directory": SITES / "sveltekit",
        "entry": "serve.mjs",
        "build_entry": "node_modules/vite/bin/vite.js",
        "preview_args": (),
        "port": 18736,
        "fixture_name": "svelte",
        "static_directory": "build",
        "seed_path": "/",
        "expected_paths": ("/", "/svelte/guide", "/api/svelte-catalog"),
        "expected_interfaces": {"/api/svelte-search": "POST"},
        "runtime_only_paths": (
            "/api/svelte-click", "/api/svelte-menu", "/api/svelte-scroll", "/api/svelte-page",
        ),
        "source_map_prefixes": ("/_app/",),
    },
    "local-interaction-boundaries": {
        "directory": SITES / "interaction-boundaries",
        "entry": "../serve_static_fixture.mjs",
        "no_build": True,
        "preview_args": (),
        "port": 18737,
        "fixture_name": "interaction",
        "static_directory": ".",
        "seed_path": "/",
        "expected_paths": ("/", "/same-origin-frame.html"),
        "expected_interfaces": {"/api/interaction-search": "POST"},
        "runtime_only_paths": ("/api/interaction-shadow", "/api/interaction-frame"),
        "asset_prefixes": ("/main.js", "/frame.js"),
        "source_map_prefixes": (),
    },
}


@dataclass
class Service:
    process: subprocess.Popen[bytes]
    log: Any


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json_lines(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def read_decoy_fixture(path: Path) -> list[str]:
    call = re.compile(r'^\s*readCandidateLabel\(("(?:\\.|[^"\\])*")\),\s*$')
    values: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = call.match(line)
        if match:
            value = json.loads(match.group(1))
            if isinstance(value, str):
                values.append(value)
    return values


def npm_cli_for(node: str) -> Path | None:
    candidates = [Path(node).resolve().parent / "node_modules" / "npm" / "bin" / "npm-cli.js"]
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if npm:
        candidates.append(Path(npm).resolve().parent / "node_modules" / "npm" / "bin" / "npm-cli.js")
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def run_logged(
    command: list[str],
    cwd: Path,
    log_path: Path,
    *,
    env: dict[str, str] | None = None,
    timeout: int = 600,
) -> tuple[int, float]:
    started = time.monotonic()
    with log_path.open("w", encoding="utf-8", newline="\n") as log:
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=timeout,
        )
    return completed.returncode, time.monotonic() - started


def ensure_dependencies(node: str, npm_cli: Path, site_dir: Path, log_path: Path, skip_install: bool) -> float:
    if (site_dir / "node_modules").is_dir():
        return 0.0
    if skip_install:
        raise RuntimeError(f"{site_dir} is missing node_modules; run npm ci or omit --skip-install")
    code, elapsed = run_logged(
        [node, str(npm_cli), "ci", "--no-audit", "--no-fund"], site_dir, log_path, timeout=900
    )
    if code != 0:
        raise RuntimeError(f"npm ci failed in {site_dir}; see {log_path}")
    return elapsed


def build_site(
    name: str,
    config: dict[str, Any],
    node: str,
    npm_cli: Path,
    out_dir: Path,
    skip_install: bool,
) -> dict[str, float]:
    build_dir = out_dir / "build"
    build_dir.mkdir(parents=True, exist_ok=True)
    if config.get("no_build"):
        return {"npm_ci_seconds": 0.0, "build_seconds": 0.0}

    site_dir = Path(config["directory"])
    install_seconds = ensure_dependencies(
        node, npm_cli, site_dir, build_dir / f"{name}-npm-ci.log", skip_install
    )
    entry = site_dir / str(config.get("build_entry", config["entry"]))
    if not entry.is_file():
        raise RuntimeError(f"framework CLI not found: {entry}; run npm ci first")
    env = os.environ.copy()
    env["NUXT_TELEMETRY_DISABLED"] = "1"
    env["NEXT_TELEMETRY_DISABLED"] = "1"
    code, build_seconds = run_logged(
        [node, str(entry), "build"], site_dir, build_dir / f"{name}-build.log", env=env
    )
    if code != 0:
        raise RuntimeError(f"{name} build failed; see {build_dir / f'{name}-build.log'}")
    return {"npm_ci_seconds": install_seconds, "build_seconds": build_seconds}


def free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def launch_service(
    name: str, config: dict[str, Any], node: str, out_dir: Path
) -> tuple[Service, int, str]:
    port = int(config.get("port") or free_loopback_port())
    site_dir = Path(config["directory"])
    entry = site_dir / str(config["entry"])
    command = [node, str(entry), *config["preview_args"], "--port", str(port)]
    if config.get("strict_port"):
        command.append("--strictPort")
    env = os.environ.copy()
    env.update({"HOST": "127.0.0.1", "PORT": str(port), "NUXT_TELEMETRY_DISABLED": "1"})
    if config.get("static_directory"):
        env["CRAWL_FIXTURE_STATIC_DIR"] = str(config["static_directory"])
        env["CRAWL_FIXTURE_NAME"] = str(config["fixture_name"])
    for key in list(env):
        if key.lower() in {"http_proxy", "https_proxy", "all_proxy"}:
            env.pop(key, None)
    env["NO_PROXY"] = "127.0.0.1,localhost"
    env["no_proxy"] = "127.0.0.1,localhost"
    log_path = out_dir / "services" / f"{name}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = log_path.open("wb")
    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
    process = subprocess.Popen(
        command,
        cwd=site_dir,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        creationflags=creationflags,
        start_new_session=(os.name != "nt"),
    )
    service = Service(process=process, log=log)
    return service, port, str(log_path)


def wait_for_service(service: Service, origin: str, log_path: str, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    last_error = "no response"
    while time.monotonic() < deadline:
        if service.process.poll() is not None:
            raise RuntimeError(f"local server exited with {service.process.returncode}; see {log_path}")
        try:
            with urlopen(origin, timeout=2) as response:
                if response.status < 500:
                    return
        except (OSError, URLError) as exc:
            last_error = str(exc)
        time.sleep(0.25)
    raise RuntimeError(f"local server did not become ready at {origin}: {last_error}; see {log_path}")


def stop_service(service: Service) -> None:
    process = service.process
    if process.poll() is None:
        if os.name == "nt":
            taskkill = shutil.which("taskkill.exe") or shutil.which("taskkill")
            if taskkill:
                subprocess.run(
                    [taskkill, "/PID", str(process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    timeout=10,
                )
            else:
                process.terminate()
        else:
            os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    service.log.close()


def path_of(raw_url: str) -> str:
    return unquote(urlsplit(raw_url).path or "/")


def status_by_path(rows: list[dict[str, Any]]) -> dict[str, list[int]]:
    result: dict[str, list[int]] = {}
    for row in rows:
        raw_url = row.get("url") or row.get("final_url")
        if not isinstance(raw_url, str):
            continue
        try:
            status = int(row["status"])
        except (KeyError, TypeError, ValueError):
            continue
        result.setdefault(path_of(raw_url).rstrip("/") or "/", []).append(status)
    return result


def crawl_site(
    name: str,
    config: dict[str, Any],
    origin: str,
    binary: Path,
    out_dir: Path,
) -> dict[str, Any]:
    target_dir = out_dir / name
    target_dir.mkdir(parents=True, exist_ok=True)
    seed = origin + str(config["seed_path"])
    report_path = target_dir / "scan-report.json"
    discovery_path = target_dir / "discoveries.jsonl"
    output_path = target_dir / "tool-output.jsonl"
    stdout_path = target_dir / "stdout.txt"
    command = [
        str(binary), "-u", seed,
        "-d", "8",
        "-c", "8",
        "--timeout", "10",
        "--max-urls", "1000",
        "--max-body", "2097152",
        "--scope", "fqdn",
        "--rl", "100",
        "--phc", "16",
        "-s", "all",
        "-v",
        "--no-fuzz",
        "--no-testfill",
        "--no-unsafe-send",
        "--no-robots",
        "--no-sitemap",
        "--no-llms",
        "--no-probe-openapi",
        "--report", str(report_path),
        "--discoveries", str(discovery_path),
        "-O", "json",
        "-o", str(output_path),
    ]
    (target_dir / "command.json").write_text(
        json.dumps({"command": command, "seed": seed, "scope": "fqdn", "site_id": name}, indent=2),
        encoding="utf-8",
    )
    started = time.monotonic()
    code, _ = run_logged(command, ROOT, stdout_path, timeout=180)
    elapsed = time.monotonic() - started
    rows = read_json_lines(output_path)
    http_rows: list[dict[str, Any]] = []
    scheduled_rows: list[dict[str, Any]] = []
    for row in rows:
        try:
            status = int(row.get("status", 0) or 0)
        except (TypeError, ValueError):
            status = 0
        if status > 0:
            http_rows.append(row)
        if status > 0 or row.get("error"):
            scheduled_rows.append(row)
    discoveries = read_json_lines(discovery_path)
    statuses = status_by_path(http_rows)
    expected = {str(path).rstrip("/") or "/" for path in config["expected_paths"]}
    expected_interfaces = {
        str(path).rstrip("/") or "/": str(method).upper()
        for path, method in config.get("expected_interfaces", {}).items()
    }
    discovered_interfaces = {
        (path_of(str(row["url"])).rstrip("/") or "/", str(row.get("method", "GET")).upper())
        for row in discoveries
        if isinstance(row.get("url"), str)
    }
    missing_interfaces = sorted(
        f"{method} {path}"
        for path, method in expected_interfaces.items()
        if (path, method) not in discovered_interfaces
    )
    succeeded = {
        path for path, values in statuses.items() if any(200 <= status < 400 for status in values)
    }
    missing = sorted(expected - succeeded)
    forbidden = sorted(
        {
            path_of(str(row["url"]))
            for row in scheduled_rows
            if isinstance(row.get("url"), str)
            and (
                any(fragment.lower() in path_of(str(row["url"])).lower() for fragment in FALSE_CANDIDATE_FRAGMENTS)
                or path_of(str(row["url"])) in set(config.get("runtime_only_paths", ()))
            )
        }
    )
    decoy_values: list[str] = []
    if config.get("decoy_fixture"):
        fixture_path = Path(config["decoy_fixture"])
        decoy_values = read_decoy_fixture(fixture_path)
        expected_decoy_count = int(config.get("expected_decoy_count", 0))
        if len(decoy_values) != expected_decoy_count:
            raise RuntimeError(
                f"{name} decoy fixture has {len(decoy_values)} calls; "
                f"expected {expected_decoy_count}: {fixture_path}"
            )
    decoy_paths = {unquote(urlsplit(value).path or value) for value in decoy_values}
    decoy_requested = sorted(
        {
            path_of(str(row["url"]))
            for row in scheduled_rows
            if isinstance(row.get("url"), str) and path_of(str(row["url"])) in decoy_paths
        }
    )
    decoy_clue_paths = {
        path_of(str(row["url"]))
        for row in rows
        if isinstance(row.get("url"), str)
        and path_of(str(row["url"])) in decoy_paths
        and row.get("confidence") == "clue"
        and row.get("evidence") == "call-candidate"
    }
    decoy_unobserved_paths = sorted(decoy_paths - decoy_clue_paths - set(decoy_requested))
    asset_prefixes = tuple(config.get("asset_prefixes", ())) + tuple(config["source_map_prefixes"])
    assets = [
        row for row in http_rows
        if isinstance(row.get("url"), str)
        and any(urlsplit(str(row["url"])).path.startswith(prefix) for prefix in asset_prefixes)
    ]
    maps = [row for row in assets if str(row.get("url", "")).lower().endswith(".map")]
    asset_paths = {
        path for path in succeeded
        if any(path.startswith(prefix) for prefix in asset_prefixes)
    }
    unexpected = sorted(succeeded - expected - asset_paths)
    analysis_failures = 0
    request_failures = 0
    completion = None
    requests_attempted = None
    report_elapsed = None
    if report_path.is_file():
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            completion_data = report.get("completion", {})
            completion = completion_data.get("status")
            requests_attempted = completion_data.get("requests_attempted")
            report_elapsed = report.get("elapsed_seconds")
            for reason in completion_data.get("reasons", []):
                if reason.get("code") == "analysis_failure":
                    analysis_failures += int(reason.get("count", 0))
                elif reason.get("code") == "request_failure":
                    request_failures += int(reason.get("count", 0))
        except (json.JSONDecodeError, AttributeError, TypeError, ValueError):
            pass
    result = {
        "site_id": name,
        "seed": seed,
        "exit_code": code,
        "elapsed_seconds": round(elapsed, 3),
        "output_rows": len(rows),
        "response_rows": len(http_rows),
        "discovery_rows": len(discoveries),
        "expected_paths": sorted(expected),
        "succeeded_paths": sorted(succeeded),
        "missing_expected_paths": missing,
        "expected_interfaces": [f"{method} {path}" for path, method in sorted(expected_interfaces.items())],
        "discovered_interfaces": [
            f"{method} {path}" for path, method in sorted(discovered_interfaces)
        ],
        "missing_interfaces": missing_interfaces,
        "unexpected_application_paths": unexpected,
        "forbidden_requested_paths": forbidden,
        "decoy_candidate_count": len(decoy_values),
        "decoy_clue_count": len(decoy_clue_paths),
        "decoy_requested_paths": decoy_requested,
        "decoy_unobserved_paths": decoy_unobserved_paths,
        "asset_responses": len(assets),
        "source_map_responses": len(maps),
        "completion": completion,
        "requests_attempted": requests_attempted,
        "report_elapsed_seconds": report_elapsed,
        "analysis_failures": analysis_failures,
        "request_failures": request_failures,
    }
    result["passed"] = (
        not missing
        and not missing_interfaces
        and not unexpected
        and not forbidden
        and not decoy_requested
        and not analysis_failures
        and not request_failures
        and (not config["source_map_prefixes"] or bool(maps))
    )
    (target_dir / "site-result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def markdown_summary(
    run_id: str, binary_hash: str, build_results: dict[str, dict[str, float]], site_results: list[dict[str, Any]]
) -> str:
    lines = [
        "# Local framework crawl regression",
        "",
        f"Run: `{run_id}`",
        f"Basecrawler SHA-256: `{binary_hash}`",
        "Network scope: loopback only; no public site targets were requested.",
        "",
        "## Build timing",
        "",
        "| Site | npm ci (s) | Build (s) |",
        "|---|---:|---:|",
    ]
    for name, timing in build_results.items():
        lines.append(f"| {name} | {timing['npm_ci_seconds']:.2f} | {timing['build_seconds']:.2f} |")
    lines.extend(
        [
            "",
            "## Crawl results",
            "",
            "| Site | Status | Runtime (s) | HTTP responses | Requests attempted | Source maps | Decoy paths: total/clue/requested/ignored | Analysis/request failures | Missing expected routes | Missing interface contracts | Unexpected paths | False candidates requested |",
            "|---|---|---:|---:|---:|---:|---:|---:|---|---|---|---|",
        ]
    )
    for result in site_results:
        missing = ", ".join(result["missing_expected_paths"]) or "—"
        missing_interfaces = ", ".join(result["missing_interfaces"]) or "—"
        unexpected = ", ".join(result["unexpected_application_paths"]) or "—"
        forbidden = ", ".join(result["forbidden_requested_paths"]) or "—"
        decoys = (
            f"{result['decoy_candidate_count']} total, {result['decoy_clue_count']} clues, "
            f"{len(result['decoy_requested_paths'])} requested, {len(result['decoy_unobserved_paths'])} ignored"
        )
        lines.append(
            f"| {result['site_id']} | {'PASS' if result['passed'] else 'FAIL'} | "
            f"{result['elapsed_seconds']:.2f} | {result['response_rows']} | "
            f"{result.get('requests_attempted', 'n/a')} | {result['source_map_responses']} | "
            f"{decoys} | "
            f"{result['analysis_failures']}/{result['request_failures']} | {missing} | {missing_interfaces} | {unexpected} | {forbidden} |"
        )
    lines.extend(
        [
            "",
        "Expected routes are counted as covered when the crawler receives a 2xx or 3xx response; form interfaces are covered when the expected method and path are preserved as discoveries. "
            "The false-candidate check examines HTTP response and request-error records; clue records may remain in discoveries without being scheduled.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Basecrawler against local framework fixtures.")
    parser.add_argument("--site", choices=("all", *SITE_CONFIG.keys()), default="all")
    parser.add_argument("--out-dir", type=Path, help="output directory; defaults under artifacts/crawler-comparison")
    parser.add_argument("--skip-install", action="store_true", help="do not run npm ci when node_modules is missing")
    args = parser.parse_args()

    node = shutil.which("node")
    if not node:
        parser.error("Node.js is required to build and serve the local fixtures")
    npm_cli = npm_cli_for(node)
    if not npm_cli:
        parser.error("npm-cli.js was not found next to Node.js; install Node.js with npm")
    go = shutil.which("go")
    if not go:
        parser.error("Go is required to build Basecrawler from the current checkout")

    selected = list(SITE_CONFIG) if args.site == "all" else [args.site]
    run_id = datetime.now().astimezone().strftime("local-framework-regression-%Y%m%d-%H%M%S")
    out_dir = (args.out_dir or ROOT / "artifacts" / "crawler-comparison" / run_id).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    build_results: dict[str, dict[str, float]] = {}
    services: list[Service] = []
    site_results: list[dict[str, Any]] = []
    temp_binary_dir: tempfile.TemporaryDirectory[str] | None = None

    try:
        for name in selected:
            build_results[name] = build_site(name, SITE_CONFIG[name], node, npm_cli, out_dir, args.skip_install)

        temp_binary_dir = tempfile.TemporaryDirectory(prefix="basecrawler-local-sites-")
        binary = Path(temp_binary_dir.name) / ("basecrawler.exe" if os.name == "nt" else "basecrawler")
        build_log = out_dir / "build" / "basecrawler-build.log"
        code, build_seconds = run_logged(
            [go, "build", "-o", str(binary), "./cmd/basecrawler"], BASECRAWLER, build_log, timeout=600
        )
        if code != 0:
            raise RuntimeError(f"Basecrawler build failed; see {build_log}")
        binary_hash = sha256_file(binary)
        (out_dir / "build" / "basecrawler-build.json").write_text(
            json.dumps({"sha256": binary_hash, "build_seconds": round(build_seconds, 3)}, indent=2),
            encoding="utf-8",
        )

        for name in selected:
            config = SITE_CONFIG[name]
            service, port, service_log = launch_service(name, config, node, out_dir)
            services.append(service)
            origin = f"http://127.0.0.1:{port}"
            wait_for_service(service, origin + str(config["seed_path"]), service_log)
            site_results.append(crawl_site(name, config, origin, binary, out_dir))

        summary = {
            "run_id": run_id,
            "basecrawler_sha256": binary_hash,
            "basecrawler_build_seconds": round(build_seconds, 3),
            "node": subprocess.check_output([node, "--version"], text=True).strip(),
            "sites": site_results,
            "passed": all(result["passed"] for result in site_results),
        }
        (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        (out_dir / "summary.md").write_text(
            markdown_summary(run_id, binary_hash, build_results, site_results), encoding="utf-8"
        )
        print(out_dir)
        print(f"PASS={summary['passed']} basecrawler_sha256={binary_hash}")
        for result in site_results:
            print(
                f"{result['site_id']}: {'PASS' if result['passed'] else 'FAIL'}, "
                f"{result['elapsed_seconds']:.2f}s, {result['response_rows']} responses, "
                f"missing={len(result['missing_expected_paths'])}, "
                f"false_requested={len(result['forbidden_requested_paths'])}"
            )
        return 0 if summary["passed"] else 1
    except (OSError, RuntimeError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        failure = {"run_id": run_id, "error": str(exc)}
        (out_dir / "failure.json").write_text(json.dumps(failure, indent=2), encoding="utf-8")
        print(f"ERROR: {exc}", file=sys.stderr)
        print(f"Partial artifacts: {out_dir}", file=sys.stderr)
        return 2
    finally:
        for service in reversed(services):
            stop_service(service)
        if temp_binary_dir is not None:
            temp_binary_dir.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
