#!/usr/bin/env python3
"""Join discovery edges and HTTP attempts without treating static sinks as execution."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from run_local_site_regression import read_json_lines


def identity(url: str, method: str = "GET") -> str:
    # Preserve origin, query order, blank values and percent-encoding.
    return str(method or "GET").upper() + " " + str(url)


def stage(source: str, evidence: str) -> str:
    if evidence == "executed-request-call" and source == "js-exec:request":
        return "runtime_request_call"
    if evidence == "executed-navigation-call" and source == "js-exec:navigation":
        return "runtime_navigation_call"
    if evidence == "executed-dependency-load" or source == "chunk:exec":
        return "runtime_dependency_load"
    if source.startswith(("js-ast:", "sourcemap:")):
        return "static_request_sink" if evidence == "request-call" else "static_candidate"
    if source == "json" or source.startswith("json:"):
        return "response_extraction"
    if source in {"a", "script", "link", "form"}:
        return "document_extraction"
    return "other_discovery"


def canonical_headers(value: dict | None) -> dict:
    return {str(k).lower(): str(v) for k, v in sorted((value or {}).items())}


def audit_records(discoveries: list[dict], attempts: list[dict], outputs: list[dict],
                  pending: list[dict] | None = None) -> dict:
    targets: dict[str, dict] = {}

    def group(url, method="GET"):
        label = identity(url, method)
        return targets.setdefault(label, {"identity": label, "url": url,
                                         "method": str(method or "GET").upper(),
                                         "discoveries": [], "attempts": [], "response_records": [],
                                         "deferred": []})

    seen = set()
    for row in discoveries:
        if not row.get("url"):
            continue
        edge = {field: row[field] for field in (
            "source", "evidence", "referer", "depth", "kind", "confidence", "tag",
            "attribute", "headers", "params", "json_body_params", "param_values") if field in row}
        edge["stage"] = stage(str(row.get("source") or ""), str(row.get("evidence") or ""))
        edge["trigger_condition"] = {
            "runtime_request_call": "Invocation reached in bounded JS replay; exact branch expression is not captured",
            "runtime_navigation_call": "Navigation invocation reached in bounded JS replay",
            "runtime_dependency_load": "Dependency loader invocation reached in bounded JS replay",
            "static_request_sink": "AST request sink identified; branch reachability is not established",
            "static_candidate": "Static candidate identified; no request invocation is established",
            "response_extraction": "URL extracted from a received response; does not establish JS continuation",
            "document_extraction": "URL extracted from a document element",
        }.get(edge["stage"], "Unknown; inspect the retained source and evidence")
        edge["response_proof"] = False
        edge_key = identity(row["url"], row.get("method")) + json.dumps(edge, sort_keys=True)
        if edge_key in seen:
            continue
        seen.add(edge_key)
        edge["edge_id"] = hashlib.sha256(edge_key.encode()).hexdigest()
        group(row["url"], row.get("method"))["discoveries"].append(edge)
    for row in attempts:
        if not row.get("requested_url"):
            continue
        attempt = dict(row)
        attempt["dispatch_basis"] = stage(str(row.get("source") or ""), str(row.get("evidence") or ""))
        attempt["header_scope"] = "explicit_per_request_inputs" if "authored_headers" in row else "not_recorded"
        group(row["requested_url"], row.get("method"))["attempts"].append(attempt)
    for row in outputs:
        if row.get("url") and (int(row.get("status") or 0) > 0 or row.get("error")):
            # The final URL may differ after redirect. Preserve the raw response
            # record; the attempt remains attached to its original requested URL.
            requested = row.get("requested_url") or row["url"]
            group(requested, row.get("method"))["response_records"].append(dict(row))
    for row in pending or []:
        if row.get("url"):
            group(row["url"], row.get("method"))["deferred"].append(dict(row))
    for target in targets.values():
        edges, records = target["discoveries"], target["attempts"]
        runtime = [edge for edge in edges if edge["stage"] == "runtime_request_call"]
        target["stages"] = {
            "discovered": bool(edges),
            "static_request_sink_observed": any(edge["stage"] == "static_request_sink" for edge in edges),
            "runtime_request_call_observed": bool(runtime),
            "runtime_dependency_load_observed": any(edge["stage"] == "runtime_dependency_load" for edge in edges),
            "attempt_recorded": bool(records),
            "http_response_observed": any(int(row.get("http_status") or 0) > 0 for row in records),
            "http_success_observed": any(200 <= int(row.get("http_status") or 0) < 400 for row in records),
        }
        for edge in runtime:
            matching = [row for row in records if "authored_headers" in row and
                        canonical_headers(row["authored_headers"]) == canonical_headers(edge.get("headers"))]
            edge["same_authored_context_success"] = any(
                200 <= int(row.get("http_status") or 0) < 400 for row in matching)
            edge["caused_dispatch"] = "not_proven_without_event_order"
        target["dispatch_bases"] = sorted({row["dispatch_basis"] for row in records})
        target["static_dispatch_with_later_or_independent_runtime_evidence"] = (
            bool(runtime) and any(row["dispatch_basis"] == "static_request_sink" for row in records))
    rows = [targets[label] for label in sorted(targets)]
    non_success = [
        {"identity": target["identity"], "http_status": int(attempt.get("http_status") or 0),
         "source": attempt.get("source"), "evidence": attempt.get("evidence"),
         "referer": attempt.get("referer"), "dispatch_basis": attempt["dispatch_basis"]}
        for target in rows for attempt in target["attempts"]
        if int(attempt.get("http_status") or 0) >= 400
    ]
    return {
        "schema_version": 1,
        "identity_policy": "exact method and URL; header contexts retained on each edge and attempt",
        "limitations": [
            "Static request-call means an AST sink, not a reached JS invocation.",
            "Headers are explicit per-request inputs, not all transport headers.",
            "Matching evidence and responses does not prove causal order.",
            "Missing authored_headers in legacy records means unknown, not empty.",
            "Runtime execution can still be blocked; absence of a call is not proof that a branch is unreachable.",
        ],
        "non_success_http_responses": non_success,
        "counts": {
            "targets": len(rows),
            "non_success_http_responses": len(non_success),
            "runtime_request_targets": sum(row["stages"]["runtime_request_call_observed"] for row in rows),
            "static_request_targets": sum(row["stages"]["static_request_sink_observed"] for row in rows),
            "http_success_targets": sum(row["stages"]["http_success_observed"] for row in rows),
        },
        "targets": rows,
    }


def audit_scan(scan_dir: Path) -> dict:
    report = json.loads((scan_dir / "scan-report.json").read_text(encoding="utf-8"))
    completion = report.get("completion", {})
    audit = audit_records(
        read_json_lines(scan_dir / "discoveries.jsonl"),
        completion.get("requests", []),
        read_json_lines(scan_dir / "tool-output.jsonl"),
        completion.get("unvisited", []))
    audit["completion"] = {field: completion.get(field) for field in ("status", "complete", "reasons")}
    audit["inputs"] = {}
    for filename in ("scan-report.json", "discoveries.jsonl", "tool-output.jsonl"):
        path = scan_dir / filename
        audit["inputs"][filename] = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    return audit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scan-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Use a new output file; previous evidence is immutable")
    result = audit_scan(args.scan_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result["counts"]))


if __name__ == "__main__":
    main()
