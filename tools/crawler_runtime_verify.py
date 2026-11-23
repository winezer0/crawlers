"""Verify captured GET calls once, without inventing URLs or following redirects."""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from crawler_runtime_replay import origin


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def captured_gets(summary, document):
    seen = set()
    for call in summary.get("captured_requests", []):
        if call.get("Method", "GET") != "GET" or call.get("Kind") == "navigation" or call.get("Evidence") != "executed-request-call":
            continue
        url = call["Raw"]
        if origin(url) != origin(document) or urlsplit(url).fragment:
            raise ValueError("Captured request is outside the allowed document origin")
        headers = {"user-agent": "BasecrawlerRuntimeVerification/1.0", "accept": "*/*",
                   **{str(k).lower(): str(v) for k, v in call.get("Headers", {}).items()}}
        key = hashlib.sha256(json.dumps(["GET", url, headers], sort_keys=True).encode()).hexdigest()
        if key not in seen:
            seen.add(key)
            yield key, url, headers


def verify(summary, document, previous=(), send=False):
    rows = {row["request_key"]: row for row in previous}
    seen = {row["request_key"] for row in rows.values() if row.get("sent")}
    opener = build_opener(NoRedirect)
    for key, url, headers in captured_gets(summary, document):
        if key in seen:
            continue
        row = {"request_key": key, "url": url, "method": "GET", "headers": headers,
               "queued": False, "sent": False, "response_received": False,
               "runtime_source_sha256": summary.get("runtime_source_sha256"),
               "coverage_accepted": False, "valid_rsc_response": False}
        if send:
            started = time.monotonic()
            try:
                request = Request(url, headers=headers)
                row["verified_at"] = datetime.now(timezone.utc).isoformat()
                row["sent"] = True
                try:
                    response = opener.open(request, timeout=20)
                except HTTPError as error:
                    response = error
                with response:
                    body = response.read(512 * 1024 + 1)
                    row.update(response_received=True, status=response.status,
                               content_type=response.headers.get("Content-Type", ""),
                               location=response.headers.get("Location"),
                               response_url=response.geturl(),
                               body_prefix_sha256=hashlib.sha256(body[:512 * 1024]).hexdigest(),
                               body_prefix_bytes=min(len(body), 512 * 1024), truncated=len(body) > 512 * 1024)
                    row["valid_rsc_response"] = (200 <= response.status < 300
                        and "text/x-component" in row["content_type"].lower()
                        and bool(body.strip()) and headers.get("rsc") == "1")
            except (OSError, ValueError) as error:
                row["error"] = str(error)
            row["elapsed_seconds"] = round(time.monotonic() - started, 6)
        rows[key] = row
    return list(rows.values())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--document-url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--previous", type=Path)
    parser.add_argument("--frozen-audit", type=Path, help="Attach each frozen target's exact and related online evidence without changing the frozen record")
    parser.add_argument("--send", action="store_true", help="Send only captured same-origin GET calls; default is a plan")
    args = parser.parse_args()
    summary = json.loads(args.summary.read_text(encoding="utf-8"))
    audit = json.loads(args.frozen_audit.read_text(encoding="utf-8")) if args.frozen_audit else None
    if audit and audit.get("runtime_source_sha256") != summary.get("runtime_source_sha256"):
        parser.error("Frozen audit and runtime summary come from different source snapshots")
    previous = json.loads(args.previous.read_text(encoding="utf-8"))["rows"] if args.previous else []
    rows = verify(summary, args.document_url, previous, args.send)
    report = {"runtime_source_sha256": summary.get("runtime_source_sha256"), "rows": rows,
              "policy": "Request/response evidence only. A successful RSC variant never closes a different frozen context."}
    if audit:
        report["frozen_targets"] = []
        for target in audit["rows"]:
            urls = {target["reference_url"], *(variant["url"] for variant in target["related_variants"])}
            evidence = [row for row in rows if row["url"] in urls]
            report["frozen_targets"].append({**target, "online_evidence": evidence,
                "online_exact_sent": any(row["sent"] and row["url"] == target["reference_url"] for row in evidence),
                "online_exact_response_received": any(row["response_received"] and row["url"] == target["reference_url"] for row in evidence)})
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"recorded": len(rows), "sent": sum(r["sent"] for r in rows),
                      "valid_rsc": sum(r["valid_rsc_response"] for r in rows)}))


if __name__ == "__main__":
    main()
