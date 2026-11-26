"""Audit every observed successful reference difference without sending requests."""
import argparse
import hashlib
import json
from pathlib import Path

from crawler_retained_acceptance import identity, save
from katana_enhanced_baseline import MODES
from run_local_site_regression import read_json_lines, sha256_file
from run_runtime_proof_matrix import browser_collection


def row_identity(row):
    request = row.get("request") or {}
    return identity(request.get("endpoint") or row.get("url") or "", request.get("method") or row.get("method") or "GET")


def audit(summary_path, runtime_path):
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    original = json.loads(Path(summary["reference_summary"]).read_text(encoding="utf-8"))
    reference_root = Path(summary["reference_summary"]).parent
    sites = {}
    for name, site in summary["sites"].items():
        browser_rows = read_json_lines(reference_root / name / "browcrawler/traffic.jsonl")
        remaining = {}
        for mode in MODES:
            metadata = site["tools"]["katana-" + mode]["run"]
            path = Path(metadata["output"])
            if sha256_file(path) != metadata["output_sha256"]:
                raise RuntimeError(f"Frozen reference modified: {path}")
            rows = read_json_lines(path)
            indexed = {row_identity(row): row for row in rows if 200 <= int(row.get("response", {}).get("status_code") or 0) < 400}
            origin = metadata["profile"]["origin"]
            shell = indexed.get(identity(origin + "/"), {}).get("response", {}).get("body")
            for gap in site["base_differences"][mode]:
                label = gap["identity"]
                raw = indexed[label]
                response = raw.get("response") or {}
                body = response.get("body")
                disposition = "observed_resource_difference"
                if gap["category"] == "rsc-request-variant":
                    disposition = "browser_supplement_required"
                elif gap["category"] == "page" and shell and body == shell:
                    disposition = "same_spa_shell_application_route_unverified"
                browser = [row for row in browser_rows if row_identity(row) == label and row.get("status") == 200]
                proof = [{"content_type": row.get("content_type"), "response_body_preserved": bool(row.get("body")),
                          "rsc_header": next((value for key, value in row.get("request_headers", {}).items() if key.lower() == "rsc"), None),
                          "task_id": row.get("task_id"), "request_headers_preserved": bool(row.get("request_headers"))}
                         for row in browser]
                item = remaining.setdefault(label, {"identity": label, "category": gap["category"],
                                                   "disposition": disposition, "modes": [], "browcrawler": proof})
                item["modes"].append({"mode": mode, "reference_valid": metadata.get("valid"),
                                      "abort_reason": metadata.get("abort_reason"),
                                      "source": raw.get("request", {}).get("source"),
                                      "reference_body_sha256": hashlib.sha256(body.encode()).hexdigest() if isinstance(body, str) else None})
        sites[name] = {"tools": {tool: {"successful": (data.get("observed_score") or data["score"])["successful"],
                                                "expected": (data.get("observed_score") or data["score"])["expected"],
                                                "seconds": data["run"]["elapsed_seconds"],
                                                "reference_valid": data["run"].get("valid")}
                                       for tool, data in site["tools"].items()},
                       "remaining_base_observed_identities": list(remaining.values()),
                       "original_brow_differences": original["sites"][name]["differences"]}
    runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
    contracts = json.loads((runtime_path.parent / "contract.json").read_text(encoding="utf-8"))["variants"]
    runtime_checks = {}
    for variant, tools in runtime["variants"].items():
        directory = runtime_path.parent / ("runtime-" + variant)
        proof = browser_collection(read_json_lines(directory / "browcrawler/traffic.jsonl"), contracts[variant], variant, "http://127.0.0.1:18891")
        runtime_checks[variant] = {"browser_collection": proof,
            "tools": {tool: {"server_confirmed": len(data["score"]["server_confirmed"]), "expected": data["score"]["expected"],
                             "seconds": data["run"]["elapsed_seconds"], "reference_reused": data["run"].get("cache_reused"),
                             "wire_requests": len(json.loads((directory / tool / "oracle.json").read_text(encoding="utf-8"))),
                             "invalid_requests": data["score"].get("invalid_requests", []),
                             "invalid_posts": data["score"]["invalid_posts"], "decoy_requests": data["score"]["decoy_requests"]}
                      for tool, data in tools.items()}}
    return {"network_requests_sent_by_audit": 0, "sites": sites, "runtime": runtime_checks,
            "limitations": ["Partial reference scans provide observed lower bounds, not complete capability acceptance",
                            "Single-document task association does not establish arbitrary multi-page initiators",
                            "Historical public Next deployment hashes remain separate",
                            "Basecrawler still lacks scroll execution and four local RSC variants; generic template filling produced invalid requests"],
            "inputs": {str(path): sha256_file(path) for path in (summary_path, runtime_path)}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--runtime-summary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise RuntimeError("Audit output must be new")
    report = audit(args.summary, args.runtime_summary)
    save(args.output, report)
    for name, site in report["sites"].items():
        print(name, "remaining observed identities", len(site["remaining_base_observed_identities"]))


if __name__ == "__main__":
    main()
