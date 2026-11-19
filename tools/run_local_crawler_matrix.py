#!/usr/bin/env python3
"""Compare Basecrawler, Katana, and Browcrawler on deterministic loopback sites."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from run_local_site_regression import (
    ROOT,
    SITE_CONFIG,
    Service,
    build_site,
    launch_service,
    npm_cli_for,
    read_json_lines,
    run_logged,
    sha256_file,
    stop_service,
    wait_for_service,
)


BASECRAWLER = ROOT / "basecrawler"
BROWCRAWLER = ROOT / "browcrawler"
KATANA = ROOT / "othercrawlers" / "katana.exe"
KATANA_BASELINES = ROOT / "artifacts" / "crawler-comparison" / "local-katana-baselines"
SKIP_DIRS = {"node_modules", ".nuxt", ".output", "dist", "coverage", "artifacts"}
BASECRAWLER_ROUNDS = 3


def fixture_fingerprint(name: str) -> str:
    config = SITE_CONFIG[name]
    site_dir = Path(config["directory"])
    digest = hashlib.sha256()
    for path in sorted(site_dir.rglob("*")):
        if not path.is_file() or any(part in SKIP_DIRS for part in path.relative_to(site_dir).parts):
            continue
        relative = path.relative_to(site_dir).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    fixture_contract = {
        "site": name,
        "seed_path": config["seed_path"],
        "expected_paths": list(config["expected_paths"]),
        "expected_interfaces": config.get("expected_interfaces", {}),
        "runtime_only_paths": list(config.get("runtime_only_paths", ())),
        "port": config.get("port"),
    }
    digest.update(json.dumps(fixture_contract, sort_keys=True).encode("utf-8"))
    return digest.hexdigest()


def normalize_key(raw_url: Any, method: Any = "GET") -> tuple[str, str] | None:
    if not isinstance(raw_url, str) or not raw_url.strip():
        return None
    parsed = urlsplit(raw_url)
    path = unquote(parsed.path or "/").rstrip("/") or "/"
    return str(method or "GET").upper(), path


def int_status(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def normalized_records(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for row in rows:
        request = row.get("request") if isinstance(row.get("request"), dict) else {}
        response = row.get("response") if isinstance(row.get("response"), dict) else {}
        raw_url = (
            request.get("endpoint")
            or request.get("url")
            or row.get("url")
            or row.get("endpoint")
        )
        method = request.get("method") or row.get("method") or "GET"
        status = int_status(response.get("status_code") or response.get("status") or row.get("status"))
        key = normalize_key(raw_url, method)
        if key:
            records.append({"key": key, "status": status, "raw": row})

        # Katana's form extractor can expose contracts separately from the
        # page request. Preserve those contracts when present in JSONL.
        form_groups = [row.get("form"), row.get("forms"), response.get("form"), response.get("forms")]
        for forms in form_groups:
            if isinstance(forms, dict):
                forms = [forms]
            if not isinstance(forms, list):
                continue
            for form in forms:
                if not isinstance(form, dict):
                    continue
                form_url = form.get("action") or form.get("url") or form.get("endpoint")
                form_method = form.get("method") or form.get("type") or "GET"
                form_key = normalize_key(form_url, form_method)
                if form_key:
                    records.append({"key": form_key, "status": 0, "raw": form})
    return records


def score_records(
    name: str, tool: str, mode: str, elapsed: float, exit_code: int,
    rows: list[dict[str, Any]], request_count: int,
) -> dict[str, Any]:
    config = SITE_CONFIG[name]
    records = normalized_records(rows)
    candidates = {record["key"] for record in records}
    successful = {
        record["key"]
        for record in records
        if 200 <= int(record["status"]) < 400
    }
    pages = {("GET", str(path).rstrip("/") or "/") for path in config["expected_paths"]}
    interfaces = {
        (str(method).upper(), str(path).rstrip("/") or "/")
        for path, method in config.get("expected_interfaces", {}).items()
    }
    runtime_paths = {
        ("GET", str(path).rstrip("/") or "/")
        for path in config.get("runtime_only_paths", ())
    }
    page_found = pages & successful
    interface_found = interfaces & candidates
    interface_sent = interfaces & successful
    runtime_found = runtime_paths & candidates
    runtime_sent = runtime_paths & successful
    return {
        "site_id": name,
        "tool": tool,
        "mode": mode,
        "elapsed_seconds": round(elapsed, 3),
        "exit_code": exit_code,
        "request_records": request_count,
        "page_targets": len(pages),
        "page_successes": len(page_found),
        "page_missing": [f"{method} {path}" for method, path in sorted(pages - page_found)],
        "interface_targets": len(interfaces),
        "interfaces_found": len(interface_found),
        "interfaces_sent": len(interface_sent),
        "interface_missing": [f"{method} {path}" for method, path in sorted(interfaces - interface_found)],
        "runtime_targets": len(runtime_paths),
        "runtime_found": len(runtime_found),
        "runtime_sent": len(runtime_sent),
        "runtime_missing": [f"{method} {path}" for method, path in sorted(runtime_paths - runtime_found)],
    }


def run_katana_baseline(
    name: str, mode: str, origin: str, katana_hash: str, out_dir: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    fingerprint = fixture_fingerprint(name)
    cache_key = hashlib.sha256(
        f"{fingerprint}|{katana_hash}|{mode}|{origin}".encode("utf-8")
    ).hexdigest()[:24]
    baseline_dir = KATANA_BASELINES / cache_key / name / mode
    baseline_dir.mkdir(parents=True, exist_ok=True)
    output_path = baseline_dir / "output.jsonl"
    metadata_path = baseline_dir / "metadata.json"
    attempt_path = baseline_dir / "attempt.json"

    if metadata_path.is_file():
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["cache_reused"] = True
        rows = read_json_lines(output_path)
        return metadata, rows
    if attempt_path.exists():
        metadata = {
            "tool": "katana",
            "mode": mode,
            "fixture_fingerprint": fingerprint,
            "katana_sha256": katana_hash,
            "exit_code": -1,
            "elapsed_seconds": None,
            "frozen": True,
            "cache_reused": False,
            "error": "An earlier baseline attempt did not write metadata; reference scan was not repeated.",
        }
        return metadata, read_json_lines(output_path)

    attempt_path.write_text(
        json.dumps({"started_at": datetime.now().astimezone().isoformat(), "frozen": True}, indent=2),
        encoding="utf-8",
    )
    seed = origin + str(SITE_CONFIG[name]["seed_path"])
    command = [
        str(KATANA), "-u", seed,
        "-d", "8", "-c", "8", "-rl", "100", "-timeout", "10", "-retry", "0",
        "-fs", "fqdn", "-jc", "-jsl", "-fx", "-j", "-silent", "-duc", "-or", "-ob",
        "-o", str(output_path),
    ]
    if mode == "headless":
        command.extend(["-hl", "-aff", "-xhr"])
    command_record = {"command": command, "seed": seed, "mode": mode, "network_scope": "loopback only"}
    command_path = baseline_dir / "command.json"
    command_path.write_text(json.dumps(command_record, indent=2), encoding="utf-8")
    log_path = baseline_dir / "stdout.txt"
    code, elapsed = run_logged(command, ROOT, log_path, timeout=600)
    metadata = {
        "tool": "katana",
        "mode": mode,
        "seed": seed,
        "fixture_fingerprint": fingerprint,
        "katana_sha256": katana_hash,
        "exit_code": code,
        "elapsed_seconds": round(elapsed, 3),
        "output_rows": len(read_json_lines(output_path)),
        "frozen": True,
        "cache_reused": False,
        "command": command,
    }
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    links_path = out_dir / name / f"katana-{mode}-baseline.json"
    links_path.parent.mkdir(parents=True, exist_ok=True)
    links_path.write_text(
        json.dumps({"baseline_dir": str(baseline_dir), "metadata": str(metadata_path)}, indent=2),
        encoding="utf-8",
    )
    return metadata, read_json_lines(output_path)


def build_binary(go: str, module_dir: Path, output: Path, log_path: Path) -> float:
    output.parent.mkdir(parents=True, exist_ok=True)
    code, elapsed = run_logged(
        [go, "build", "-o", str(output), "./cmd/" + ("basecrawler" if module_dir == BASECRAWLER else "browcrawler")],
        module_dir,
        log_path,
        timeout=600,
    )
    if code != 0:
        raise RuntimeError(f"build failed in {module_dir}; see {log_path}")
    return elapsed


def run_basecrawler(
    name: str, origin: str, binary: Path, run_dir: Path, round_number: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    target_dir = run_dir / name / f"basecrawler-round-{round_number}"
    target_dir.mkdir(parents=True, exist_ok=True)
    seed = origin + str(SITE_CONFIG[name]["seed_path"])
    output_path = target_dir / "output.jsonl"
    discoveries_path = target_dir / "discoveries.jsonl"
    report_path = target_dir / "scan-report.json"
    command = [
        str(binary), "-u", seed, "-d", "8", "-c", "8", "--timeout", "10",
        "--max-urls", "1000", "--max-body", "2097152", "--scope", "fqdn",
        "--rl", "100", "--phc", "16", "-s", "all", "--no-fuzz", "--no-testfill",
        "--no-unsafe-send", "--no-robots", "--no-sitemap", "--no-llms",
        "--no-probe-openapi", "--report", str(report_path), "--discoveries",
        str(discoveries_path), "-O", "json", "-o", str(output_path),
    ]
    (target_dir / "command.json").write_text(
        json.dumps({"command": command, "seed": seed, "network_scope": "loopback only"}, indent=2),
        encoding="utf-8",
    )
    code, elapsed = run_logged(command, ROOT, target_dir / "stdout.txt", timeout=600)
    rows = read_json_lines(output_path) + read_json_lines(discoveries_path)
    result = score_records(
        name, "basecrawler", f"static-round-{round_number}", elapsed, code, rows,
        sum(1 for row in read_json_lines(output_path) if int_status(row.get("status")) > 0),
    )
    result["round"] = round_number
    result["frozen_reference"] = False
    (target_dir / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result, rows


def run_browcrawler(
    name: str, origin: str, binary: Path, run_dir: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    target_dir = run_dir / name / "browcrawler"
    target_dir.mkdir(parents=True, exist_ok=True)
    seed = origin + str(SITE_CONFIG[name]["seed_path"])
    traffic_path = target_dir / "traffic.jsonl"
    discovery_path = target_dir / "discovered.jsonl"
    report_path = target_dir / "crawl-report.json"
    metrics_path = target_dir / "metrics.jsonl"
    saved_result = target_dir / "result.json"
    if saved_result.is_file() and traffic_path.is_file() and discovery_path.is_file():
        result = json.loads(saved_result.read_text(encoding="utf-8"))
        result["saved_run_reused"] = True
        return result, read_json_lines(traffic_path) + read_json_lines(discovery_path)
    command = [
        str(binary), "--config", str(BROWCRAWLER / "config.example.yaml"),
        "--url", seed, "--scope-mode", "fqdn", "--depth", "8", "--max-pages", "1000",
        "--basecrawler-max-urls", "1000", "--interactions", "10", "--settle-ms", "600",
        "--retries", "0", "--backend", "obscura", "--upstream", "none",
        "--out", str(traffic_path), "--discovered", str(discovery_path),
        "--report", str(report_path), "--metrics", str(metrics_path),
    ]
    (target_dir / "command.json").write_text(
        json.dumps({"command": command, "seed": seed, "network_scope": "loopback only", "forms_enabled": True}, indent=2),
        encoding="utf-8",
    )
    code, elapsed = run_logged(command, BROWCRAWLER, target_dir / "stdout.txt", timeout=900)
    traffic_rows = read_json_lines(traffic_path)
    discovery_rows = read_json_lines(discovery_path)
    rows = traffic_rows + discovery_rows
    result = score_records(
        name, "browcrawler", "browser", elapsed, code, rows,
        len(traffic_rows),
    )
    result["form_submission_enabled"] = True
    result["interaction_limit_per_page"] = 10
    (target_dir / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result, rows


def markdown_report(
    run_id: str, katana_version: str, katana_hash: str, build_times: dict[str, Any],
    site_results: list[dict[str, Any]],
) -> str:
    lines = [
        "# Local crawler comparison matrix",
        "",
        f"Run: `{run_id}`",
        "Network scope: loopback-only Nuxt and React fixture servers.",
        f"Katana: `{katana_version}`; SHA-256 `{katana_hash}`. Its standard and headless outputs are frozen and reused by fixture fingerprint.",
        f"Basecrawler rounds: {BASECRAWLER_ROUNDS}; the best round is selected by successful expected routes, then interface coverage, then response count.",
        "Browcrawler: one browser run per fixture; Obscura backend, interactions enabled, form submission enabled, upstream proxy disabled.",
        "",
        "## Build time",
        "",
        "| Component | Build seconds |",
        "|---|---:|",
    ]
    for component, seconds in build_times.items():
        if isinstance(seconds, dict):
            for phase, duration in seconds.items():
                lines.append(f"| {component} {phase.replace('_', ' ')} | {duration:.2f} |")
        else:
            lines.append(f"| {component} | {seconds:.2f} |")
    for site in site_results:
        lines.extend(["", f"## {site['site_id']}", ""])
        lines.extend([
            "| Tool / mode | Runtime (s) | Page routes (2xx/3xx) | Form interfaces (found / submitted) | Runtime-only APIs (found / 2xx/3xx) | Missing targets |",
            "|---|---:|---:|---:|---:|---|",
        ])
        for result in site["comparison_rows"]:
            missing = result["page_missing"] + result["interface_missing"]
            if result["mode"] != "static-round-1" and result["mode"] != "static-round-2" and result["mode"] != "static-round-3":
                missing += result["runtime_missing"]
            lines.append(
                f"| {result['tool']} / {result['mode']} | {result['elapsed_seconds']:.2f} | "
                f"{result['page_successes']}/{result['page_targets']} | "
                f"{result['interfaces_found']}/{result['interface_targets']} / "
                f"{result['interfaces_sent']}/{result['interface_targets']} | "
                f"{result['runtime_found']}/{result['runtime_targets']} / "
                f"{result['runtime_sent']}/{result['runtime_targets']} | "
                f"{', '.join(missing) or '—'} |"
            )
        best = site["basecrawler_best"]
        lines.extend([
            "",
            f"Basecrawler best round: **{best['round']}** ({best['page_successes']}/{best['page_targets']} page routes, "
            f"{best['interfaces_found']}/{best['interface_targets']} form interfaces, {best['elapsed_seconds']:.2f}s).",
            f"Frozen Katana cache reused: `{site['katana_cache_reused']}`.",
        ])
    lines.extend([
        "",
        "## Interpretation",
        "",
        "Page route success requires an HTTP 2xx/3xx response. A form contract counts as found when method and action are preserved; submission is reported separately. Runtime-only endpoints are surfaced separately because the HTML/source does not contain their full URL. This keeps Basecrawler's no-browser scope comparable without treating browser-only behavior as a static parser failure.",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare local Basecrawler, Katana, and Browcrawler runs.")
    parser.add_argument("--site", choices=("all", *SITE_CONFIG.keys()), default="all")
    parser.add_argument("--out-dir", type=Path, help="output directory; defaults under artifacts/crawler-comparison")
    parser.add_argument("--skip-install", action="store_true", help="do not run npm ci if dependencies are missing")
    args = parser.parse_args()

    node = shutil.which("node")
    go = shutil.which("go")
    if not node or not go:
        parser.error("Node.js and Go are required")
    npm_cli = npm_cli_for(node)
    if not npm_cli:
        parser.error("npm-cli.js was not found next to Node.js")
    if not KATANA.is_file():
        parser.error(f"Katana Windows binary is missing: {KATANA}")

    selected = list(SITE_CONFIG) if args.site == "all" else [args.site]
    if args.out_dir:
        out_dir = args.out_dir.resolve()
        run_id = out_dir.name
    else:
        run_id = datetime.now().astimezone().strftime("local-crawler-matrix-%Y%m%d-%H%M%S")
        out_dir = (ROOT / "artifacts" / "crawler-comparison" / run_id).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    build_dir = out_dir / "build"
    build_dir.mkdir(parents=True, exist_ok=True)

    build_times: dict[str, Any] = {}
    services: list[Service] = []
    temp_bin_dir: tempfile.TemporaryDirectory[str] | None = None
    site_results: list[dict[str, Any]] = []
    katana_hash = sha256_file(KATANA)
    katana_version_output = subprocess.check_output(
        [str(KATANA), "-version"], text=True, stderr=subprocess.STDOUT
    )
    katana_version_output = re.sub(r"\x1b\[[0-9;]*m", "", katana_version_output)
    version_match = re.search(r"Current version:\s*(v[\w.-]+)", katana_version_output)
    katana_version = version_match.group(1) if version_match else katana_version_output.strip().splitlines()[-1]
    try:
        for name in selected:
            build_times[name] = build_site(name, SITE_CONFIG[name], node, npm_cli, out_dir, args.skip_install)

        temp_bin_dir = tempfile.TemporaryDirectory(prefix="local-crawler-matrix-")
        temp_bin = Path(temp_bin_dir.name)
        base_binary = temp_bin / ("basecrawler.exe" if os.name == "nt" else "basecrawler")
        brow_binary = temp_bin / ("browcrawler.exe" if os.name == "nt" else "browcrawler")
        build_times["basecrawler"] = build_binary(go, BASECRAWLER, base_binary, build_dir / "basecrawler-build.log")
        build_times["browcrawler"] = build_binary(go, BROWCRAWLER, brow_binary, build_dir / "browcrawler-build.log")
        (build_dir / "binary-hashes.json").write_text(
            json.dumps({"basecrawler": sha256_file(base_binary), "browcrawler": sha256_file(brow_binary)}, indent=2),
            encoding="utf-8",
        )

        origins: dict[str, str] = {}
        for name in selected:
            service, port, log_path = launch_service(name, SITE_CONFIG[name], node, out_dir)
            services.append(service)
            origin = f"http://127.0.0.1:{port}"
            wait_for_service(service, origin + str(SITE_CONFIG[name]["seed_path"]), log_path)
            origins[name] = origin

        for name in selected:
            origin = origins[name]
            comparison_rows: list[dict[str, Any]] = []
            baselines: dict[str, Any] = {}
            for mode in ("standard", "headless"):
                metadata, katana_rows = run_katana_baseline(name, mode, origin, katana_hash, out_dir)
                baselines[mode] = metadata
                comparison_rows.append(
                    score_records(
                        name, "katana", mode, float(metadata.get("elapsed_seconds") or 0),
                        int(metadata.get("exit_code", -1)), katana_rows, len(katana_rows),
                    )
                )

            base_rounds: list[dict[str, Any]] = []
            for round_number in range(1, BASECRAWLER_ROUNDS + 1):
                result, _ = run_basecrawler(name, origin, base_binary, out_dir, round_number)
                base_rounds.append(result)
                comparison_rows.append(result)

            brow_result, _ = run_browcrawler(name, origin, brow_binary, out_dir)
            comparison_rows.append(brow_result)
            best_base = max(
                base_rounds,
                key=lambda row: (
                    row["page_successes"], row["interfaces_found"], row["request_records"], -row["elapsed_seconds"]
                ),
            )
            cache_reused = all(bool(row.get("cache_reused")) for row in baselines.values())
            site_result = {
                "site_id": name,
                "fixture_fingerprint": fixture_fingerprint(name),
                "origin": origin,
                "katana_baselines": baselines,
                "katana_cache_reused": cache_reused,
                "basecrawler_rounds": base_rounds,
                "basecrawler_best": best_base,
                "browcrawler": brow_result,
                "comparison_rows": comparison_rows,
            }
            site_results.append(site_result)
            (out_dir / name / "comparison.json").write_text(json.dumps(site_result, indent=2), encoding="utf-8")

        summary = {
            "run_id": run_id,
            "network_scope": "loopback only",
            "katana_version": katana_version,
            "katana_sha256": katana_hash,
            "basecrawler_rounds": BASECRAWLER_ROUNDS,
            "node": subprocess.check_output([node, "--version"], text=True).strip(),
            "build_times": build_times,
            "sites": site_results,
        }
        (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        (out_dir / "summary.md").write_text(
            markdown_report(run_id, katana_version, katana_hash, build_times, site_results),
            encoding="utf-8",
        )
        print(out_dir)
        for site in site_results:
            print(f"{site['site_id']}: Basecrawler best round {site['basecrawler_best']['round']}")
            for row in site["comparison_rows"]:
                print(
                    f"  {row['tool']}/{row['mode']}: {row['elapsed_seconds']:.2f}s, "
                    f"pages={row['page_successes']}/{row['page_targets']}, "
                    f"forms={row['interfaces_found']}/{row['interface_targets']}, "
                    f"runtime={row['runtime_found']}/{row['runtime_targets']}"
                )
        return 0
    except (OSError, RuntimeError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        failure = {"run_id": run_id, "error": str(exc)}
        (out_dir / "failure.json").write_text(json.dumps(failure, indent=2), encoding="utf-8")
        print(f"ERROR: {exc}", file=sys.stderr)
        print(f"Partial artifacts: {out_dir}", file=sys.stderr)
        return 2
    finally:
        for service in reversed(services):
            stop_service(service)
        if temp_bin_dir:
            temp_bin_dir.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
