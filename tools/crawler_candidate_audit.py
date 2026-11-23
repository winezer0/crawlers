"""Audit saved crawler candidates and HTTP evidence without network requests."""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

RESOURCE_EXTENSIONS = {".js", ".mjs", ".css", ".map", ".png", ".jpg", ".jpeg", ".svg", ".webp", ".avif", ".ico", ".gif", ".woff", ".woff2", ".ttf", ".mp4", ".webm", ".pdf"}
CLUE_EVIDENCE = {"srcset-alternative", "source-file-metadata", "filename-metadata", "route-template-metadata", "rendered-text"}


def read_json(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else default


def read_jsonl(path: Path):
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def records_for(row):
    return row.get("source_records") or [row]


def actionable_record(record):
    return record.get("confidence", "") in ("", "certain", "interface") and record.get("evidence", "") not in CLUE_EVIDENCE


def category(row, evidence):
    records = records_for(row)
    if not any(actionable_record(record) for record in records):
        return "clue_or_template"
    method = row.get("method", "GET").upper()
    if method != "GET":
        return "api_or_form_contract"
    parts = urlsplit(row["url"])
    if "_rsc" in parse_qs(parts.query):
        return "protocol_request"
    mime = str((evidence or {}).get("content_type", "")).lower().split(";", 1)[0]
    if mime.startswith(("image/", "font/", "video/", "audio/")) or mime in ("application/javascript", "text/javascript", "text/css", "application/pdf"):
        return "resource"
    if Path(parts.path).suffix.lower() in RESOURCE_EXTENSIONS or any(record.get("kind") == "chunk" for record in records):
        return "resource"
    if any(record.get("source") in ("img", "video", "script", "meta-og:image", "meta-twitter:image") and actionable_record(record) for record in records):
        return "resource"
    if mime == "application/json" or any(record.get("evidence") in ("request-call", "executed-request-call") for record in records):
        return "api_or_form_contract"
    if mime in ("text/html", "text/markdown") or any(record.get("source") in ("a", "sitemap", "llms.txt", "seed", "meta-og:url") for record in records):
        return "page_candidate"
    return "unresolved_candidate"


def audit(run_dir: Path, replay_path: Path | None = None):
    rows = read_json(run_dir / "candidates.json", [])
    completion = read_json(run_dir / "report.json", {}).get("completion", {})
    attempts = completion.get("requests", [])
    if not attempts:
        attempts = read_json(run_dir / "run.json", {}).get("crawl_completion", {}).get("requests", [])
    attempted = {(r.get("method", "GET").upper(), r.get("requested_url", r.get("url", ""))): r for r in attempts}
    pending = {(r.get("method", "GET").upper(), r.get("url", "")): r for r in completion.get("unvisited", [])}
    verification = collections.defaultdict(list)
    for record in read_jsonl(run_dir.parent / "verification.jsonl"):
        verification[record.get("url", "")].append(record)
    replay = collections.defaultdict(list)
    for edge in read_jsonl(replay_path) if replay_path else []:
        replay[(edge.get("method", "GET"), edge["url"])].append(edge)
    output = []
    for row in rows:
        raw = row["url"]
        key = (row.get("method", "GET").upper(), raw)
        # Saved HTTP verification is GET-only; never use it to validate a POST.
        evidence = verification.get(raw, []) if key[0] == "GET" else []
        complete = [r for r in evidence if not r.get("error") and not r.get("truncated") and r.get("status")]
        latest = complete[-1] if complete else None
        request = attempted.get(key)
        status = request.get("http_status", 0) if request else 0
        verified = "unverified"
        if status and 200 <= status < 300 and not request.get("truncated"):
            verified = "crawl_2xx"
        elif latest and 200 <= latest["status"] < 300:
            verified = "saved_get_2xx"
        elif status == 404 or (latest and latest["status"] == 404):
            verified = "http_404_not_parser_proof"
        elif status or latest:
            verified = "other_http_status"
        kind = category(row, latest)
        source_records = records_for(row)
        reviewed_edges = replay.get(key, [])
        text_edges = [edge for edge in reviewed_edges if edge.get("confidence") == "clue" and edge.get("evidence") in ("rendered-text", "source-file-metadata")]
        # A text occurrence must not downgrade independent href or request evidence.
        review = "not_reviewed"
        if text_edges:
            independent = any(record.get("source") != "next-flight" and actionable_record(record) for record in source_records)
            strong_replay = any(edge.get("confidence", "") in ("", "certain") for edge in reviewed_edges)
            review = "text_occurrence_with_independent_target" if independent or strong_replay else "defer_text_or_filename_pending_other_referers"
        parts = urlsplit(raw)
        image_query = parse_qs(parts.query)
        # Image optimizer grouping is a diagnostic only, not request equivalence.
        image_group = image_query.get("url", [""])[0] if "w" in image_query and "q" in image_query else ""
        output.append({
            "method": key[0], "url": raw, "category": kind, "verification": verified,
            "attempted": request is not None, "queued": pending.get(key, {}).get("queued"),
            "saved_get_status": latest.get("status") if latest else None,
            "sources": sorted({record.get("source", "") for record in source_records}),
            "evidence": sorted({record.get("evidence", "") for record in source_records}),
            "actionable": any(actionable_record(record) for record in source_records),
            "frontend_review": review,
            "image_representation_group": image_group,
            "referers": sorted({parent for record in source_records for parent in record.get("referers", [])}),
        })
    counts = collections.Counter(row["category"] for row in output)
    groups = collections.defaultdict(set)
    for row in output:
        if row["image_representation_group"]:
            groups[row["image_representation_group"]].add(row["url"])
    summary = {
        "candidate_method_urls": len(output), "categories": dict(sorted(counts.items())),
        "verification": dict(collections.Counter(row["verification"] for row in output)),
        "actionable": sum(row["actionable"] for row in output),
        "attempted_candidate_method_urls": sum(row["attempted"] for row in output),
        "image_variant_urls": sum(len(urls) for urls in groups.values()),
        "image_source_groups": len(groups),
        "image_variant_excess": sum(max(0, len(urls)-1) for urls in groups.values()),
        "frontend_review": dict(collections.Counter(row["frontend_review"] for row in output)),
        "actionable_text_or_filename_review_urls": [row["url"] for row in output if row["actionable"] and row["frontend_review"] == "defer_text_or_filename_pending_other_referers"],
        "policy": "Offline only. Categories are candidate roles, not truth labels. 404 does not prove a parser error; 2xx does not prove business validity. Image grouping does not merge request identities.",
    }
    return summary, output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True, help="saved basecrawler directory")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--replay", type=Path, help="optional offline parser source-edge output")
    args = parser.parse_args()
    summary, rows = audit(args.run_dir, args.replay)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    with (args.output / "candidates-audit.jsonl").open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False)+"\n")
    print(json.dumps(summary, ensure_ascii=True))


if __name__ == "__main__":
    main()
