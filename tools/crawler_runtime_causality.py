"""Compare offline request and observer evidence against a frozen reference.

This tool performs no network I/O. A layout error is associated only with the
element that was actually read, never with every URL missing from a page.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from urllib.parse import parse_qsl, urljoin, urlsplit

from crawler_runtime_replay import Frontend, digest, save


PROTOCOL_HEADERS = {
    "rsc", "next-url", "next-router-prefetch", "next-router-segment-prefetch",
    "next-router-state-tree", "x-deployment-id",
}


def headers(raw):
    return {k.lower(): str(v) for k, v in raw.items() if k.lower() in PROTOCOL_HEADERS}


def resource(url):
    part = urlsplit(url)
    return (part.scheme.lower(), part.netloc.lower(), part.path or "/",
            tuple(sorted((k, v) for k, v in parse_qsl(part.query, keep_blank_values=True) if k != "_rsc")))


def calls(summary, url):
    return [r for r in summary.get("captured_requests", [])
            if r.get("Evidence") == "executed-request-call" and r.get("Kind") != "navigation"
            and r.get("Method", "GET") == "GET" and resource(r["Raw"]) == resource(url)]


def diagnostics(summary, document):
    events, failures = [], []
    for index, item in enumerate(summary.get("runtime_diagnostics", [])):
        if item["kind"] not in ("geometry_observation", "layout_dependency"):
            continue
        data = json.loads(item["message"])
        target = data.get("target") or (data.get("geometry_read") or {}).get("target") or {}
        href = target.get("href")
        row = {"diagnostic_index": index, "target": target,
               "url": urljoin(document, href) if href else None}
        if item["kind"] == "geometry_observation":
            row.update(action=data["action"], sequence=data["sequence"],
                       root_margin=data["rootMargin"], thresholds=data["thresholds"],
                       details=data.get("details"),
                       stack=data.get("stack", ""))
            events.append(row)
        else:
            row.update(reason=data["reason"], category=data["category"],
                       stack=(data.get("geometry_read") or {}).get("stack", ""))
            failures.append(row)
    return events, failures


def compare(reference, records, before, after, html, document):
    parsed = Frontend(document)
    parsed.feed(html)
    before_events, before_failures = diagnostics(before, document)
    after_events, after_failures = diagnostics(after, document)
    rows = []
    for url in reference["missing"]:
        proof = []
        for index, record in enumerate(records, 1):
            request = record.get("request", {})
            if request.get("endpoint") == url and request.get("method", "GET") == "GET":
                h = headers(request.get("headers", {}))
                response = record.get("response", {})
                segment = h.get("next-router-segment-prefetch")
                proof.append({"line": index, "timestamp": record.get("timestamp"),
                              "headers": h, "source": request.get("source"),
                              "status": response.get("status_code"),
                              "response_body_recorded": any(response.get(k) is not None for k in ("body", "body_file")),
                              "protocol_phase": "route_tree_prefetch" if segment == "/_tree" else "page_segment_prefetch" if segment else "unclassified",
                              "trigger_evidence": "prefetch_header" if h.get("next-router-prefetch") == "1" else "trigger_not_recorded"})
        old, new = calls(before, url), calls(after, url)
        before_urls, after_urls = {r["Raw"] for r in old}, {r["Raw"] for r in new}
        def related(items):
            return [item for item in items if item.get("url") and resource(item["url"]) == resource(url)]
        events, failures = related(after_events), related(after_failures)
        variants = []
        for call in new:
            current = headers(call.get("Headers", {}))
            variants.append({"url": call["Raw"], "headers": current,
                             "reference_context_differences": [
                                 {k: {"reference": item["headers"].get(k), "replay": current.get(k)}
                                  for k in sorted(set(item["headers"]) | set(current))
                                  if item["headers"].get(k) != current.get(k)} for item in proof]})
        rows.append({"reference_url": url, "method": "GET", "reference_records": proof,
                     "link_targets": sorted({link for link in parsed.links if resource(link) == resource(url)}),
                     "before_exact_call": url in before_urls, "after_exact_call": url in after_urls,
                     "new_related_call_urls": sorted(after_urls - before_urls),
                     "before_related_call_count": len(old), "after_related_call_count": len(new),
                     "after_call_variants": variants,
                     "before_observer_events": related(before_events), "after_observer_events": events,
                     "before_direct_layout_failures": related(before_failures), "after_direct_layout_failures": failures,
                     "diagnosis": "request_call_observed_response_unverified" if new else
                                  "direct_geometry_dependency_observed" if failures else
                                  "observer_registered_request_not_observed" if events else
                                  "no_target_observer_or_call_evidence",
                     "causal_claim": "not_proven", "response_confirmed": False,
                     "coverage_accepted": False})
    reference_deployments = sorted({p["headers"]["x-deployment-id"] for r in rows for p in r["reference_records"] if p["headers"].get("x-deployment-id")})
    replay_deployments = sorted({headers(r.get("Headers", {}))["x-deployment-id"] for r in after.get("captured_requests", []) if headers(r.get("Headers", {})).get("x-deployment-id")})
    return {"targets": len(rows), "rows": rows,
            "reference_deployment_ids": reference_deployments, "replay_deployment_ids": replay_deployments,
            "snapshot_deployment_mismatch": bool(reference_deployments and replay_deployments and set(reference_deployments).isdisjoint(replay_deployments)),
            "before_http_calls": before.get("captured_http_calls", 0),
            "after_http_calls": after.get("captured_http_calls", 0),
            "new_related_call_targets": sum(bool(r["new_related_call_urls"]) for r in rows),
            "after_exact_call_targets": sum(r["after_exact_call"] for r in rows),
            "diagnoses": dict(Counter(r["diagnosis"] for r in rows)),
            "before_unassociated_layout_failures": [r for r in before_failures if not r["url"]],
            "after_unassociated_layout_failures": [r for r in after_failures if not r["url"]],
            "before_runtime_state": before.get("runtime_state", {}),
            "after_runtime_state": after.get("runtime_state", {}),
            "before_execution_seconds": before.get("execution_seconds"),
            "after_execution_seconds": after.get("execution_seconds"),
            "network_requests": 0, "coverage_accepted": False,
            "policy": "Prefetch headers prove prefetch intent, not its event trigger. Different deployments and RSC hashes are not equivalent response evidence. Unassociated errors are not assigned to every target. No causal claim follows merely from fewer errors."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("reference-effective", "reference-results", "before", "after", "html", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--document-url", required=True)
    args = parser.parse_args()
    paths = {name: getattr(args, name) for name in ("reference_effective", "reference_results", "before", "after", "html")}
    if args.output.resolve() in {path.resolve() for path in paths.values()}:
        parser.error("Output must not overwrite an input")
    bodies = {name: path.read_bytes() for name, path in paths.items()}
    result = compare(json.loads(bodies["reference_effective"]),
                     [json.loads(line) for line in bodies["reference_results"].decode("utf-8").splitlines() if line.strip()],
                     json.loads(bodies["before"]), json.loads(bodies["after"]), bodies["html"].decode("utf-8"), args.document_url)
    result["inputs"] = {name: {"path": str(paths[name].resolve()), "sha256": digest(body)} for name, body in bodies.items()}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    save(args.output, result)
    print(json.dumps({k: result[k] for k in ("targets", "before_http_calls", "after_http_calls", "new_related_call_targets", "after_exact_call_targets", "diagnoses")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
