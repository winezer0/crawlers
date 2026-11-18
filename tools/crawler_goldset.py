#!/usr/bin/env python3
"""Build a provenance-aware, HTTP-verified URL gold set for crawler runs."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import json
import re
import threading
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin, urlsplit, urlunsplit

try:
    from .crawler_compare import ROOT, TARGETS, Target, in_scope, normalize_url
except ImportError:  # Running as `python tools/crawler_goldset.py`.
    from crawler_compare import ROOT, TARGETS, Target, in_scope, normalize_url


DEFAULT_TARGETS = (
    "framework-vue,framework-react,framework-angular,"
    "framework-nextjs,framework-sveltekit"
)
DEFAULT_OUTPUT_ROOT = ROOT / "artifacts" / "crawler-comparison"
USER_AGENT = "CrawlerBenchmark/1.0 (public documentation URL verification)"
STATIC_EXTENSIONS = {
    ".7z", ".avif", ".bmp", ".css", ".eot", ".gif", ".gz", ".ico",
    ".jpeg", ".jpg", ".js", ".mjs", ".map", ".mp3", ".mp4", ".otf",
    ".pdf", ".png", ".svg", ".tar", ".ttf", ".wasm", ".webm", ".webp",
    ".woff", ".woff2", ".zip",
}
PAGE_MEDIA_TYPES = {"text/html", "text/markdown", "application/xhtml+xml"}
API_MEDIA_TYPES = {"application/json", "application/graphql-response+json"}
MARKDOWN_LINK_RE = re.compile(r"\]\(([^)\s]+)(?:\s+['\"][^)]*['\"])?\)")
API_DOC_PATH_RE = re.compile(r"/(?:api|api-reference|reference)(?:/|\.|$)", re.I)


@dataclass(frozen=True)
class FetchResult:
    status: int | None
    content_type: str
    final_url: str
    method: str
    error: str = ""


class AnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        href = dict(attrs).get("href")
        if href:
            self.links.append(href)


class HostRateLimiter:
    def __init__(self, requests_per_second: float) -> None:
        self.interval = 1.0 / max(requests_per_second, 0.1)
        self._lock = threading.Lock()
        self._next_at = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            delay = max(0.0, self._next_at - now)
            self._next_at = max(now, self._next_at) + self.interval
        if delay:
            time.sleep(delay)


def parse_links(body: str, content_type: str) -> list[str]:
    if "html" in content_type or "xhtml" in content_type:
        parser = AnchorParser()
        parser.feed(body)
        return parser.links
    return MARKDOWN_LINK_RE.findall(body)


def canonicalize_doc_link(raw_url: str, source_url: str, target: Target) -> str | None:
    resolved = normalize_url(urljoin(source_url, raw_url))
    if not resolved or not in_scope(resolved, target):
        return None
    parts = urlsplit(resolved)
    path = parts.path
    host = (urlsplit(target.seed).hostname or "").lower()
    if path.lower().endswith(".md"):
        if host == "vuejs.org":
            path = path[:-3] + ".html"
        else:
            path = path[:-3]
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, ""))


def parse_sitemap(body: bytes) -> tuple[bool, list[str]]:
    if body.startswith(b"\x1f\x8b"):
        body = gzip.decompress(body)
    root = ET.fromstring(body)
    root_name = root.tag.rsplit("}", 1)[-1].lower()
    is_index = root_name == "sitemapindex"
    locations = [
        (element.text or "").strip()
        for element in root.iter()
        if element.tag.rsplit("}", 1)[-1].lower() in {"loc"}
        and (element.text or "").strip()
    ]
    return is_index, locations


def fetch_source(url: str, timeout: float) -> tuple[str, bytes, str]:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,text/plain,application/xml,*/*"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return (
            response.geturl(),
            response.read(32 * 1024 * 1024 + 1),
            response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower(),
        )


def add_source(
    records: dict[str, dict[str, Any]],
    raw_url: str,
    source_type: str,
    source_document: str,
    target: Target,
) -> None:
    normalized = canonicalize_doc_link(raw_url, source_document, target)
    if not normalized:
        return
    row = records.setdefault(
        normalized,
        {"url": normalized, "source_types": set(), "source_documents": set()},
    )
    row["source_types"].add(source_type)
    row["source_documents"].add(source_document)


def sitemap_urls(
    target: Target, timeout: float, errors: list[dict[str, str]]
) -> tuple[dict[str, set[str]], set[str]]:
    host = (urlsplit(target.seed).hostname or "").lower()
    sitemap_docs: set[str] = set()
    visited_docs: set[str] = set()
    pages: dict[str, set[str]] = {}
    robots_url = f"{urlsplit(target.seed).scheme}://{host}/robots.txt"
    candidates: list[str] = []
    try:
        _, body, _ = fetch_source(robots_url, timeout)
        for line in body.decode("utf-8", "replace").splitlines():
            if line.lower().startswith("sitemap:"):
                candidates.append(line.split(":", 1)[1].strip())
    except Exception as exc:
        errors.append({"source": robots_url, "error": f"{type(exc).__name__}: {exc}"})

    root_sitemap = f"{urlsplit(target.seed).scheme}://{host}/sitemap.xml"
    if root_sitemap not in candidates:
        candidates.append(root_sitemap)

    queue = list(dict.fromkeys(candidates))
    while queue and len(visited_docs) < 100:
        sitemap_url = queue.pop(0)
        normalized_sitemap = normalize_url(sitemap_url)
        if not normalized_sitemap or normalized_sitemap in visited_docs:
            continue
        visited_docs.add(normalized_sitemap)
        try:
            final_url, body, _ = fetch_source(normalized_sitemap, timeout)
            final_normalized = normalize_url(final_url) or normalized_sitemap
            sitemap_docs.add(final_normalized)
            is_index, locations = parse_sitemap(body)
            for location in locations:
                canonical = normalize_url(urljoin(final_url, location))
                if not canonical or not in_scope(canonical, target):
                    continue
                if is_index:
                    queue.append(canonical)
                else:
                    pages.setdefault(canonical, set()).add(final_normalized)
        except Exception as exc:
            errors.append({"source": normalized_sitemap, "error": f"{type(exc).__name__}: {exc}"})
    return pages, sitemap_docs


def llms_index_urls(
    target: Target, timeout: float, errors: list[dict[str, str]]
) -> tuple[dict[str, set[str]], set[str]]:
    seed_parts = urlsplit(target.seed)
    host_root = f"{seed_parts.scheme}://{seed_parts.netloc}"
    paths = ["/llms.txt"]
    if seed_parts.path.startswith("/docs/"):
        section = seed_parts.path.split("/", 2)[1]
        paths.insert(0, f"/{section}/llms.txt")
    page_urls: dict[str, set[str]] = {}
    documents: set[str] = set()
    for path in dict.fromkeys(paths):
        index_url = host_root + path
        try:
            final_url, body, content_type = fetch_source(index_url, timeout)
            if "text/plain" not in content_type and "text/markdown" not in content_type:
                continue
            documents.add(normalize_url(final_url) or final_url)
            text = body.decode("utf-8", "replace")
            for raw_link in parse_links(text, "text/markdown"):
                canonical = canonicalize_doc_link(raw_link, final_url, target)
                if canonical:
                    page_urls.setdefault(canonical, set()).add(normalize_url(final_url) or final_url)
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                errors.append({"source": index_url, "error": f"HTTPError: {exc.code}"})
        except Exception as exc:
            errors.append({"source": index_url, "error": f"{type(exc).__name__}: {exc}"})
    return page_urls, documents


def seed_links(target: Target, timeout: float, errors: list[dict[str, str]]) -> tuple[set[str], str]:
    try:
        final_url, body, content_type = fetch_source(target.seed, timeout)
        text = body.decode("utf-8", "replace")
        urls = {
            canonical
            for raw in parse_links(text, content_type)
            if (canonical := canonicalize_doc_link(raw, final_url, target))
        }
        seed = canonicalize_doc_link(target.seed, target.seed, target)
        if seed:
            urls.add(seed)
        return urls, normalize_url(final_url) or target.seed
    except Exception as exc:
        errors.append({"source": target.seed, "error": f"{type(exc).__name__}: {exc}"})
        return set(), target.seed


def route_type(url: str, content_type: str = "") -> str:
    path = urlsplit(url).path.lower()
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type in API_MEDIA_TYPES and (
        re.search(r"/(?:api|graphql)(?:/|$)", path) or path.endswith("/graphql")
    ):
        return "api_endpoint"
    if media_type in PAGE_MEDIA_TYPES:
        if API_DOC_PATH_RE.search(path):
            return "api_documentation"
        return "page"
    if media_type in API_MEDIA_TYPES:
        return "structured_data"
    return "other"


def static_asset(url: str, content_type: str) -> bool:
    path = urlsplit(url).path.lower()
    media_type = content_type.split(";", 1)[0].strip().lower()
    if path.endswith(tuple(STATIC_EXTENSIONS)):
        return True
    return (
        media_type.startswith(("image/", "font/", "audio/", "video/"))
        or media_type in {
            "text/css", "application/javascript", "text/javascript",
            "application/wasm", "application/octet-stream", "application/pdf",
        }
    )


def verify_url(url: str, timeout: float, limiter: HostRateLimiter) -> FetchResult:
    limiter.wait()
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,text/markdown,application/json,*/*"}
    request = urllib.request.Request(url, headers=headers, method="HEAD")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return FetchResult(
                response.status,
                response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower(),
                response.geturl(),
                "HEAD",
            )
    except urllib.error.HTTPError as exc:
        if exc.code not in {403, 405, 501}:
            return FetchResult(
                exc.code,
                exc.headers.get("Content-Type", "").split(";", 1)[0].strip().lower(),
                exc.geturl(),
                "HEAD",
                f"HTTP {exc.code}",
            )
    except Exception as exc:
        return FetchResult(None, "", url, "HEAD", f"{type(exc).__name__}: {exc}")

    limiter.wait()
    fallback = urllib.request.Request(
        url,
        headers={**headers, "Range": "bytes=0-0"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(fallback, timeout=timeout) as response:
            response.read(1)
            return FetchResult(
                response.status,
                response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower(),
                response.geturl(),
                "GET-range",
            )
    except urllib.error.HTTPError as exc:
        return FetchResult(
            exc.code,
            exc.headers.get("Content-Type", "").split(";", 1)[0].strip().lower(),
            exc.geturl(),
            "GET-range",
            f"HTTP {exc.code}",
        )
    except Exception as exc:
        return FetchResult(None, "", url, "GET-range", f"{type(exc).__name__}: {exc}")


def load_candidates(run_dir: Path, target_ids: set[str]) -> dict[str, set[str]]:
    path = run_dir / "urls.csv"
    if not path.is_file():
        raise ValueError(f"run directory has no urls.csv: {run_dir}")
    candidates: dict[str, set[str]] = {site_id: set() for site_id in target_ids}
    with path.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            site_id = row.get("site_id", "")
            if site_id not in target_ids or row.get("in_scope", "").lower() != "true":
                continue
            url = normalize_url(row.get("normalized_url", ""))
            if url:
                candidates[site_id].add(url)
    return candidates


def collect_sources(
    targets: dict[str, Target], timeout: float
) -> tuple[dict[str, dict[str, dict[str, Any]]], dict[str, dict[str, Any]]]:
    records_by_site: dict[str, dict[str, dict[str, Any]]] = {}
    source_counts: dict[str, dict[str, Any]] = {}
    for site_id, target in targets.items():
        records: dict[str, dict[str, Any]] = {}
        errors: list[dict[str, str]] = []
        sitemap_set, sitemap_docs = sitemap_urls(target, timeout, errors)
        llms_set, llms_docs = llms_index_urls(target, timeout, errors)
        seed_set, seed_doc = seed_links(target, timeout, errors)
        for url, docs in sitemap_set.items():
            for source_document in docs:
                add_source(records, url, "sitemap", source_document, target)
        for url, docs in llms_set.items():
            for source_document in docs:
                add_source(records, url, "llms_index", source_document, target)
        for url in seed_set:
            add_source(records, url, "seed_navigation", seed_doc, target)
        records_by_site[site_id] = records
        source_counts[site_id] = {
            "sitemap_documents": len(sitemap_docs),
            "sitemap_urls": len(sitemap_set),
            "llms_documents": len(llms_docs),
            "llms_urls": len(llms_set),
            "seed_links": max(0, len(seed_set) - 1),
            "unique_source_urls": len(records),
            "errors": errors,
        }
    return records_by_site, source_counts


def verify_all(
    urls: dict[str, set[str]],
    targets: dict[str, Target],
    timeout: float,
    workers_per_host: int,
    rate: float,
) -> dict[tuple[str, str], FetchResult]:
    work: list[tuple[str, str]] = [
        (site_id, url)
        for site_id, site_urls in urls.items()
        for url in sorted(site_urls)
    ]
    limiters = {
        site_id: HostRateLimiter(rate)
        for site_id in targets
    }
    results: dict[tuple[str, str], FetchResult] = {}
    max_workers = max(1, workers_per_host * len(targets))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(verify_url, url, timeout, limiters[site_id]): (site_id, url)
            for site_id, url in work
        }
        completed = 0
        for future in as_completed(futures):
            key = futures[future]
            try:
                results[key] = future.result()
            except Exception as exc:
                results[key] = FetchResult(None, "", key[1], "HEAD", f"{type(exc).__name__}: {exc}")
            completed += 1
            if completed % 100 == 0 or completed == len(work):
                print(f"HTTP verification {completed}/{len(work)}", flush=True)
    return results


def csv_write(path: Path, columns: list[str], rows: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def build_report(
    output_dir: Path,
    run_dir: Path,
    targets: dict[str, Target],
    records_by_site: dict[str, dict[str, dict[str, Any]]],
    candidates_by_site: dict[str, set[str]],
    source_counts: dict[str, dict[str, Any]],
    checks: dict[tuple[str, str], FetchResult],
    started_at: dt.datetime,
    timeout: float,
    workers_per_host: int,
    rate: float,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    gold_rows: list[dict[str, Any]] = []
    candidate_rows: list[dict[str, Any]] = []
    metrics_rows: list[dict[str, Any]] = []
    valid_gold_by_site: dict[str, set[str]] = {}

    for site_id, target in targets.items():
        records = records_by_site[site_id]
        candidates = candidates_by_site[site_id]
        valid_gold: set[str] = set()
        route_candidates = 0
        route_accessible = 0
        for url, record in sorted(records.items()):
            check = checks[(site_id, url)]
            result_class = route_type(url, check.content_type)
            is_success = check.status is not None and 200 <= check.status < 400
            is_route_record = result_class in {"page", "api_documentation", "api_endpoint"}
            is_gold = is_success and is_route_record
            if is_gold:
                valid_gold.add(url)
            gold_rows.append({
                "site_id": site_id,
                "url": url,
                "source_types": ";".join(sorted(record["source_types"])),
                "source_documents": ";".join(sorted(record["source_documents"])),
                "route_type": result_class,
                "http_status": check.status or "",
                "content_type": check.content_type,
                "final_url": check.final_url,
                "verify_method": check.method,
                "error": check.error,
                "gold_valid": is_gold,
            })

        for url in sorted(candidates):
            check = checks[(site_id, url)]
            result_class = route_type(url, check.content_type)
            is_success = check.status is not None and 200 <= check.status < 400
            is_asset = static_asset(url, check.content_type)
            if not is_asset:
                route_candidates += 1
                if is_success and result_class in {"page", "api_documentation", "api_endpoint"}:
                    route_accessible += 1
            candidate_rows.append({
                "site_id": site_id,
                "url": url,
                "http_status": check.status or "",
                "content_type": check.content_type,
                "final_url": check.final_url,
                "verify_method": check.method,
                "route_type": result_class,
                "static_asset": is_asset,
                "in_goldset": url in records and url in valid_gold,
                "error": check.error,
            })

        tp = len(candidates & valid_gold)
        valid_gold_by_site[site_id] = valid_gold
        precision = tp / route_candidates if route_candidates else 0.0
        recall = tp / len(valid_gold) if valid_gold else 0.0
        counts = source_counts[site_id]
        metrics_rows.append({
            "site_id": site_id,
            "sitemap_urls": counts["sitemap_urls"],
            "llms_urls": counts["llms_urls"],
            "seed_links": counts["seed_links"],
            "source_unique_urls": counts["unique_source_urls"],
            "verified_gold_urls": len(valid_gold),
            "candidate_urls": len(candidates),
            "route_candidates_ex_assets": route_candidates,
            "http_accessible_route_candidates": route_accessible,
            "true_positives": tp,
            "gold_precision": f"{precision:.4f}",
            "gold_recall": f"{recall:.4f}",
        })

    csv_write(output_dir / "goldset.csv", [
        "site_id", "url", "source_types", "source_documents", "route_type",
        "http_status", "content_type", "final_url", "verify_method", "error", "gold_valid",
    ], gold_rows)
    csv_write(output_dir / "candidate-verification.csv", [
        "site_id", "url", "http_status", "content_type", "final_url", "verify_method",
        "route_type", "static_asset", "in_goldset", "error",
    ], candidate_rows)
    csv_write(output_dir / "metrics.csv", [
        "site_id", "sitemap_urls", "llms_urls", "seed_links", "source_unique_urls",
        "verified_gold_urls", "candidate_urls", "route_candidates_ex_assets",
        "http_accessible_route_candidates", "true_positives", "gold_precision", "gold_recall",
    ], metrics_rows)

    report = [
        "# Verified crawler URL gold set", "",
        f"Run directory: `{run_dir}`", f"Started: `{started_at.isoformat()}`", "",
        "The authoritative set combines same-host URLs from `robots.txt`/sitemap documents, the official `llms.txt` documentation index when available, and links on the configured seed page. Every unique source URL and every unique in-scope basecrawler candidate is checked with HEAD, falling back to a one-byte ranged GET when HEAD is rejected.", "",
        "A URL is counted as a verified gold page/API record only when its response is 2xx/3xx and its content type is HTML, Markdown, or XHTML; a JSON response is counted as an API endpoint only when its path is under `/api/` or `/graphql`. API reference documentation pages are kept as documentation pages, not mislabeled as live API endpoints.", "",
        "`gold_precision` is source-backed precision: verified source URLs that also appear in basecrawler candidates divided by non-static-asset candidates. `gold_recall` is the share of all verified source-backed gold pages/API records present in the candidate set. Sources can omit valid but unlisted URLs, so these metrics describe this explicit source-defined gold set, not every possible route on a host.", "",
        f"Verification timeout: {timeout:g}s; maximum start rate: {rate:g} requests/second per site; workers per site: {workers_per_host}.", "",
        "| Site | Sitemap URLs | Official index links | Seed links | Verified gold | Route candidates | True positives | Gold precision | Gold recall |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in metrics_rows:
        report.append(
            f"| {row['site_id']} | {row['sitemap_urls']} | {row['llms_urls']} | {row['seed_links']} | "
            f"{row['verified_gold_urls']} | {row['route_candidates_ex_assets']} | {row['true_positives']} | "
            f"{float(row['gold_precision']):.1%} | {float(row['gold_recall']):.1%} |"
        )
    report.extend(["", "Source collection errors are listed in `metadata.json`. Full URL-level provenance and HTTP results are in `goldset.csv`; candidate checks are in `candidate-verification.csv`."])
    (output_dir / "summary.md").write_text("\n".join(report) + "\n", encoding="utf-8")

    metadata = {
        "run_dir": str(run_dir),
        "started_at": started_at.isoformat(),
        "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "targets": {site_id: target.seed for site_id, target in targets.items()},
        "timeout_seconds": timeout,
        "workers_per_site": workers_per_host,
        "max_requests_per_second_per_site": rate,
        "source_counts": source_counts,
        "source_policy": ["robots_sitemap", "root_sitemap_fallback", "official_llms_index", "seed_page_links"],
    }
    (output_dir / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")


def self_test() -> int:
    target = TARGETS["framework-react"]
    assert parse_links("- [Quick Start](https://react.dev/learn.md)\n", "text/markdown") == ["https://react.dev/learn.md"]
    assert canonicalize_doc_link("https://react.dev/learn.md", "https://react.dev/llms.txt", target) == "https://react.dev/learn"
    assert canonicalize_doc_link("/other", "https://evil.example/docs", target) is None
    vue = TARGETS["framework-vue"]
    assert canonicalize_doc_link("/guide/introduction.md", "https://vuejs.org/llms.txt", vue) == "https://vuejs.org/guide/introduction.html"
    is_index, locations = parse_sitemap(b"<sitemapindex><sitemap><loc>https://x.test/a.xml</loc></sitemap></sitemapindex>")
    assert is_index and locations == ["https://x.test/a.xml"]
    is_index, locations = parse_sitemap(b"<urlset><url><loc>https://x.test/route</loc></url></urlset>")
    assert not is_index and locations == ["https://x.test/route"]
    parser = AnchorParser()
    parser.feed('<a href="/route">Route</a><a href="https://other.test/x">Offsite</a>')
    assert parser.links == ["/route", "https://other.test/x"]
    assert route_type("https://x.test/docs/api-reference", "text/html") == "api_documentation"
    assert route_type("https://x.test/api/items", "application/json") == "api_endpoint"
    assert static_asset("https://x.test/app.js", "text/javascript")
    print("self-test passed")
    return 0


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build an HTTP-verified crawler gold set from authoritative site sources.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("self-test", help="run offline parser and classification checks")
    build = sub.add_parser("build", help="collect and verify official URLs and basecrawler candidates")
    build.add_argument("--run-dir", type=Path, required=True, help="crawler comparison run directory with urls.csv")
    build.add_argument("--output-dir", type=Path, help="output directory for goldset and metrics")
    build.add_argument("--targets", default=DEFAULT_TARGETS, help="comma-separated site IDs")
    build.add_argument("--timeout", type=float, default=12.0)
    build.add_argument("--workers-per-site", type=int, default=4)
    build.add_argument("--rate", type=float, default=2.0, help="maximum request starts per second per site")
    build.add_argument("--dry-run", action="store_true", help="collect sources and print counts without HTTP verification")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = make_parser()
    args = parser.parse_args(argv)
    if args.command == "self-test":
        return self_test()
    if args.workers_per_site < 1 or args.rate <= 0 or args.timeout <= 0:
        parser.error("workers, rate, and timeout must be positive")
    site_ids = [part.strip() for part in args.targets.split(",") if part.strip()]
    unknown = sorted(set(site_ids) - set(TARGETS))
    if unknown:
        parser.error(f"unknown target(s): {', '.join(unknown)}")
    targets = {site_id: TARGETS[site_id] for site_id in site_ids}
    started_at = dt.datetime.now(dt.timezone.utc)
    records_by_site, source_counts = collect_sources(targets, args.timeout)
    candidates_by_site = load_candidates(args.run_dir, set(site_ids))
    all_urls = {
        site_id: set(records_by_site[site_id]) | candidates_by_site[site_id]
        for site_id in site_ids
    }
    print("site_id\tsitemap\tllms\tseed_links\tunique_sources\tcandidates\tchecks")
    for site_id in site_ids:
        counts = source_counts[site_id]
        print(
            f"{site_id}\t{counts['sitemap_urls']}\t{counts['llms_urls']}\t{counts['seed_links']}\t"
            f"{counts['unique_source_urls']}\t{len(candidates_by_site[site_id])}\t{len(all_urls[site_id])}"
        )
    if args.dry_run:
        return 0
    checks = verify_all(all_urls, targets, args.timeout, args.workers_per_site, args.rate)
    output_dir = args.output_dir or DEFAULT_OUTPUT_ROOT / f"verified-goldset-{started_at:%Y%m%d}"
    build_report(
        output_dir, args.run_dir, targets, records_by_site, candidates_by_site,
        source_counts, checks, started_at, args.timeout, args.workers_per_site, args.rate,
    )
    print(f"summary: {output_dir / 'summary.md'}")
    print(f"goldset: {output_dir / 'goldset.csv'}")
    print(f"metrics: {output_dir / 'metrics.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
