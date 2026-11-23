#!/usr/bin/env python3
"""Independent Next.js scenarios; records failures without changing the parser."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from urllib.parse import parse_qsl, urlencode, urlsplit

from run_local_site_regression import (
    ROOT, BASECRAWLER, SITE_CONFIG, build_site, crawl_site, launch_service,
    npm_cli_for, read_json_lines, run_logged, sha256_file, stop_service, wait_for_service,
)

# Freeze these expectations before execution. Holdouts must not drive same-run patches.
SCENARIOS = (
    {"id": "app-ssr", "seed": "/matrix/ssr", "targets": ["/api/matrix/ssr"]},
    {"id": "pages-ssr", "seed": "/matrix-pages", "targets": ["/api/matrix/pages-ssr"]},
    {"id": "dynamic", "seed": "/matrix/dynamic/dynamic-proof", "targets": ["/api/matrix/dynamic-proof"]},
    {"id": "client-effect", "seed": "/matrix/csr", "targets": ["/api/matrix/csr"]},
    {"id": "lazy-import", "seed": "/matrix/lazy", "targets": ["/api/matrix/lazy"], "holdout": True},
    {"id": "response-chain", "seed": "/matrix/response",
     "targets": ["/api/matrix/bootstrap", "/api/matrix/response-followup?token=local%2Bproof"], "holdout": True},
    {"id": "svg-identity", "seed": "/matrix/svg", "targets": ["/api/matrix/svg"], "holdout": True},
)
NEGATIVE = "/api/matrix/decoy-not-a-request"


def key(url: str, method: str = "GET") -> str:
    parsed = urlsplit(url)
    query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)))
    return method.upper() + " " + (parsed.path or "/") + ("?" + query if query else "")


def evaluate(case: dict, outputs: list[dict], discoveries: list[dict]) -> dict:
    expected = {key(url) for url in case["targets"]}
    def row_key(row):
        return key(str(row["url"]), str(row.get("method") or "GET"))
    found = {row_key(row) for row in discoveries + outputs if row.get("url")}
    sent = Counter()
    attempted = set()
    sources: dict[str, set[str]] = {}
    for row in outputs:
        if not row.get("url"):
            continue
        target = row_key(row)
        sources.setdefault(target, set()).add(str(row.get("source") or "unknown"))
        status = int(row.get("status") or 0)
        if status > 0 or row.get("error"):
            attempted.add(target)
        if 200 <= status < 400:
            sent[target] += 1
    # Only the explicit negative control is a confirmed false positive.
    # Other API results remain unclassified, never silently counted as valid.
    negative_found = sorted(target for target in found if urlsplit(target.split(" ", 1)[1]).path == NEGATIVE)
    negative_sent = sorted(target for target in attempted if urlsplit(target.split(" ", 1)[1]).path == NEGATIVE)
    unclassified = sorted(target for target in attempted
                          if urlsplit(target.split(" ", 1)[1]).path.startswith("/api/matrix/")
                          and target not in expected and target not in negative_sent)
    return {
        "expected": sorted(expected), "found": sorted(expected & found),
        "sent_successfully": sorted(expected & sent.keys()),
        "missing_discovery": sorted(expected - found), "missing_success": sorted(expected - sent.keys()),
        "negative_discovered": negative_found, "negative_requested": negative_sent,
        "unclassified_api_requests": unclassified,
        "successful_target_response_counts": {target: sent[target] for target in sorted(expected)},
        "target_sources": {target: sorted(sources.get(target, set())) for target in sorted(expected)},
        "passed": expected <= sent.keys() and not negative_sent and not unclassified,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--skip-build", action="store_true", help="reuse an already built Next fixture")
    args = parser.parse_args()
    out = (args.out_dir or ROOT / "artifacts/crawler-comparison" /
           datetime.now().strftime("next-scenario-matrix-%Y%m%d-%H%M%S")).resolve()
    out.mkdir(parents=True, exist_ok=True)
    if (out / "contract.json").exists():
        raise RuntimeError("Use a new output directory; previous scenario records are immutable")
    contract = {"scenarios": SCENARIOS, "negative": NEGATIVE, "reference": "No Katana rescan; new fixture scenarios have no frozen Katana measurement"}
    contract_bytes = json.dumps(contract, sort_keys=True, indent=2).encode()
    (out / "contract.json").write_bytes(contract_bytes)
    fixture = Path(SITE_CONFIG["local-next"]["directory"])
    digest = hashlib.sha256()
    for directory in ("app", "pages"):
        for path in sorted((fixture / directory).rglob("*")):
            if path.is_file():
                digest.update(path.relative_to(fixture).as_posix().encode())
                digest.update(path.read_bytes())
    for name in ("package.json", "package-lock.json", "next.config.js"):
        digest.update(name.encode())
        digest.update((fixture / name).read_bytes())
    node, go = shutil.which("node"), shutil.which("go")
    if not node or not go:
        raise RuntimeError("Node and Go are required")
    config = dict(SITE_CONFIG["local-next"])
    build = {} if args.skip_build else build_site("local-next", config, node, npm_cli_for(node), out, True)
    service = None
    results = []
    try:
        with tempfile.TemporaryDirectory(prefix="next-scenarios-") as tmp:
            binary = Path(tmp) / "basecrawler.exe"
            code, duration = run_logged([go, "build", "-o", str(binary), "./cmd/basecrawler"],
                                        BASECRAWLER, out / "basecrawler-build.log")
            if code:
                raise RuntimeError("Basecrawler build failed")
            binary_hash = sha256_file(binary)
            service, port, log = launch_service("next-scenarios", config, node, out)
            origin = f"http://127.0.0.1:{port}"
            wait_for_service(service, origin, log)
            for case in SCENARIOS:
                name = case["id"]
                current = dict(config, seed_path=case["seed"], expected_paths=(case["seed"],),
                               expected_interfaces={}, runtime_only_paths=())
                scan = crawl_site(name, current, origin, binary, out)
                rows = read_json_lines(out / name / "tool-output.jsonl")
                discoveries = read_json_lines(out / name / "discoveries.jsonl")
                scored = evaluate(case, rows, discoveries)
                scored.update({"id": name, "holdout": case.get("holdout", False),
                               "elapsed_seconds": scan["elapsed_seconds"],
                               "response_rows": scan["response_rows"], "discovery_rows": scan["discovery_rows"],
                               "exit_code": scan["exit_code"], "completion": scan["completion"],
                               "analysis_failures": scan["analysis_failures"], "request_failures": scan["request_failures"],
                               "scan_report": f"{name}/scan-report.json",
                               "stdout": f"{name}/stdout.txt"})
                scored["passed"] = (scored["passed"] and scan["exit_code"] == 0
                                    and not scan["missing_expected_paths"] and not scan["analysis_failures"]
                                    and not scan["request_failures"])
                results.append(scored)
                print(json.dumps(scored), flush=True)
    finally:
        if service:
            stop_service(service)
    summary = {"contract_sha256": hashlib.sha256(contract_bytes).hexdigest(),
               "fixture_sha256": digest.hexdigest(), "basecrawler_sha256": binary_hash,
               "build": build, "basecrawler_build_seconds": duration,
               "scope": "URL discovery and successful sending; does not prove browser runtime execution",
               "reference": contract["reference"], "results": results,
               "passed": all(result["passed"] for result in results)}
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(out)
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
