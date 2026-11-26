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

from crawler_evidence_audit import audit_scan
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
CONTEXT_SCENARIOS = (
    {"id": "default-spread", "seed": "/context-regression/defaults",
     "targets": ["/api/context-regression/default"], "api_prefix": "/api/context-regression/",
     "required_runtime": True},
    {"id": "shared-instances", "seed": "/context-regression/instances",
     "targets": ["/api/context-regression/alpha", "/api/context-regression/beta"],
     "api_prefix": "/api/context-regression/", "required_runtime": True},
    {"id": "click", "seed": "/context-regression/click", "targets": ["/api/context-regression/click"],
     "required_runtime": True,
     "api_prefix": "/api/context-regression/"},
    {"id": "shared-pages", "seed": "/context-regression/pages",
     "targets": ["/api/context-regression/default", "/api/context-regression/alpha", "/api/context-regression/beta"],
     "api_prefix": "/api/context-regression/", "required_runtime": True,
     "shared_script_cases": ["default-spread", "shared-instances"]},
    {"id": "interaction-state", "seed": "/context-regression/state",
     "targets": ["/api/context-regression/state"], "required_runtime": True,
     "api_prefix": "/api/context-regression/",
     "pending_targets": ["/api/context-regression/state-" + suffix
                         for suffix in ("hidden", "inert", "display", "visibility", "pointer")]},
)
NEGATIVE = "/api/matrix/decoy-not-a-request"
FIXTURE_BRANCH_CONDITIONS = {
    "client-effect": "ClientProbe mode falls through lazy/response/svg checks to the CSR fetch",
    "lazy-import": "ClientProbe mode === lazy; await dynamic import then invoke run",
    "response-chain": "ClientProbe mode === response; fetch bootstrap then await JSON and fetch data.next",
    "svg-identity": "ClientProbe mode === svg; SVGElement instance and className.baseVal === probe",
}


def key(url: str, method: str = "GET") -> str:
    parsed = urlsplit(url)
    query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)))
    return method.upper() + " " + (parsed.path or "/") + ("?" + query if query else "")


def evaluate_shared_scripts(outputs: list[dict], references: list[list[dict]]) -> dict:
    def script_rows(rows):
        return [row for row in rows if row.get("url") and
                urlsplit(row["url"]).path.endswith((".js", ".mjs")) and
                int(row.get("status") or 0) > 0]
    sets = [{key(row["url"], row.get("method") or "GET") for row in script_rows(rows)
             if 200 <= int(row["status"]) < 400} for rows in references]
    shared = set.intersection(*sets) if sets else set()
    actual = Counter(key(row["url"], row.get("method") or "GET") for row in script_rows(outputs))
    return {"shared_scripts": sorted(shared),
            "download_counts": {target: actual[target] for target in sorted(shared)},
            "passed": bool(shared) and all(actual[target] == 1 for target in shared)}


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
                          if urlsplit(target.split(" ", 1)[1]).path.startswith(case.get("api_prefix", "/api/matrix/"))
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



def compare_extra_targets(previous: dict, results: list[dict], audits: dict[str, dict]) -> dict:
    """Compare each prior extra with current evidence; never rescan a reference."""
    rows = []
    current = {row["id"]: row for row in results}
    for case in previous["results"]:
        name = case["id"]
        if name not in current:
            continue
        audit = audits[name]
        targets = {key(target["url"], target["method"]): target for target in audit["targets"]}
        before = {key(row["identity"].split(" ", 1)[1], row["identity"].split(" ", 1)[0]): row
                  for row in case.get("extra_api_attribution", [])}
        for label in case.get("unclassified_api_requests", []):
            prior = before.get(label, {})
            target = targets.get(label, {})
            attempts = target.get("attempts", [])
            deferred = target.get("deferred", [])
            conditional = any(edge.get("conditional") for edge in target.get("discoveries", []))
            parents_not_requested = (
                bool(prior.get("parents")) and
                all(not targets.get(key(parent), {}).get("attempts") for parent in prior["parents"]))
            if attempts:
                disposition = "still_requested"
            elif conditional and any(row.get("reason") == "conditional_execution_unresolved" for row in deferred):
                disposition = "retained_until_execution"
            elif "response_extraction" in prior.get("dispatch_bases", []) and parents_not_requested:
                disposition = "unselected_parent_response_not_requested"
            else:
                disposition = "not_requested_without_sufficient_disposition_evidence"
            rows.append({"scenario": name, "target": label,
                         "before_dispatch_bases": prior.get("dispatch_bases", []),
                         "before_parents": prior.get("parents", []),
                         "after_attempt_count": len(attempts),
                         "after_conditional_evidence": conditional,
                         "after_deferred_reasons": sorted({row.get("reason", "") for row in deferred}),
                         "disposition": disposition})
    return {"prior_extra_count": len(rows),
            "remaining_requests": sum(row["after_attempt_count"] > 0 for row in rows),
            "items": rows,
            "limitation": "No claim that a deferred branch URL is globally invalid or unreachable"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--compare-summary", type=Path, help="audit each extra from a previous immutable matrix summary")
    parser.add_argument("--suite", choices=("matrix", "context"), default="matrix")
    parser.add_argument("--skip-build", action="store_true", help="reuse an already built Next fixture")
    parser.add_argument("--port", type=int, help="serve the same build on an isolated loopback port")
    args = parser.parse_args()
    previous = json.loads(args.compare_summary.read_text(encoding="utf-8")) if args.compare_summary else None
    scenarios = CONTEXT_SCENARIOS if args.suite == "context" else SCENARIOS
    out = (args.out_dir or ROOT / "artifacts/crawler-comparison" /
           datetime.now().strftime("next-scenario-matrix-%Y%m%d-%H%M%S")).resolve()
    out.mkdir(parents=True, exist_ok=True)
    if (out / "contract.json").exists():
        raise RuntimeError("Use a new output directory; previous scenario records are immutable")
    contract = {"scenarios": scenarios, "negative": NEGATIVE, "reference": "No Katana rescan; new fixture scenarios have no frozen Katana measurement"}
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
    if args.port is not None:
        if not 1 <= args.port <= 65535:
            parser.error("--port must be between 1 and 65535")
        config["port"] = args.port
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
            for case in scenarios:
                name = case["id"]
                current = dict(config, seed_path=case["seed"], expected_paths=(case["seed"],),
                               expected_interfaces={}, runtime_only_paths=())
                scan = crawl_site(name, current, origin, binary, out)
                rows = read_json_lines(out / name / "tool-output.jsonl")
                discoveries = read_json_lines(out / name / "discoveries.jsonl")
                scored = evaluate(case, rows, discoveries)
                if case.get("shared_script_cases"):
                    check = evaluate_shared_scripts(rows, [
                        read_json_lines(out / reference / "tool-output.jsonl")
                        for reference in case["shared_script_cases"]])
                    scored["shared_script_downloads"] = check
                    scored["passed"] = scored["passed"] and check["passed"]
                audit = audit_scan(out / name)
                known_targets = {}
                for scenario in scenarios:
                    for target in scenario["targets"]:
                        known_targets.setdefault("GET " + origin + target, []).append(scenario["id"])
                extra = []
                expected_labels = {"GET " + origin + target for target in case["targets"]}
                for target in audit["targets"]:
                    if not urlsplit(target["url"]).path.startswith(case.get("api_prefix", "/api/matrix/")):
                        continue
                    label = target["identity"]
                    target["fixture_contract"] = {
                        "expected_for_current_seed": label in expected_labels,
                        "known_scenarios": known_targets.get(label, []),
                        "authored_trigger_conditions": {
                            mode: FIXTURE_BRANCH_CONDITIONS[mode] for mode in known_targets.get(label, [])
                            if mode in FIXTURE_BRANCH_CONDITIONS},

                        "source": "authored local fixture contract, not runtime reachability proof",
                    }
                    if target["attempts"] and label not in expected_labels:
                        extra.append({
                            "identity": label, "known_scenarios": known_targets.get(label, []),
                            "dispatch_bases": target["dispatch_bases"],
                            "runtime_request_call_observed": target["stages"]["runtime_request_call_observed"],
                            "parents": sorted({row.get("referer", "") for row in target["attempts"]}),
                            "classification": "other_fixture_mode" if label in known_targets else "unclassified",
                        })
                audit["extra_api_requests"] = extra
                audit["scenario_id"] = name
                (out / name / "evidence-audit.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
                runtime_labels = {target["identity"] for target in audit["targets"]
                                  if target["stages"]["runtime_request_call_observed"]}
                scored["non_success_http_responses"] = audit["non_success_http_responses"]
                scored["evidence_audit"] = f"{name}/evidence-audit.json"
                scored["expected_runtime_calls"] = sorted(expected_labels & runtime_labels)
                scored["expected_without_runtime_call_evidence"] = sorted(expected_labels - runtime_labels)
                scored["extra_api_attribution"] = extra
                if case.get("required_runtime"):
                    scored["passed"] = scored["passed"] and not scored["expected_without_runtime_call_evidence"]
                if case.get("pending_targets"):
                    pending_labels = {"GET " + origin + target for target in case["pending_targets"]}
                    pending = [target for target in audit["targets"] if target["identity"] in pending_labels]
                    scored["retained_pending_targets"] = sorted(
                        target["identity"] for target in pending if
                        any(edge.get("conditional") and edge.get("confidence") == "clue"
                            for edge in target["discoveries"]) and
                        any(row.get("reason") == "conditional_execution_unresolved"
                            for row in target["deferred"]) and not target["attempts"])
                    scored["passed"] = scored["passed"] and set(scored["retained_pending_targets"]) == pending_labels

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
    if previous is not None:
        audits = {row["id"]: json.loads((out / row["evidence_audit"]).read_text(encoding="utf-8"))
                  for row in results}
        comparison = compare_extra_targets(previous, results, audits)
        (out / "prior-extra-comparison.json").write_text(json.dumps(comparison, indent=2), encoding="utf-8")
        summary["prior_extra_comparison"] = "prior-extra-comparison.json"
        summary["compared_summary"] = str(args.compare_summary.resolve())
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(out)
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
