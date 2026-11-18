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
import tempfile
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections import deque
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
PAGE_MEDIA_TYPES = {"text/html", "text/markdown", "application/xhtml+xml", "text/plain"}
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


def is_llms_index_url(raw_url: str) -> bool:
    return urlsplit(raw_url).path.rstrip("/").rsplit("/", 1)[-1].lower() == "llms.txt"


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
    page_urls: dict[str, set[str]] = {}
    documents: set[str] = set()
    queue: list[tuple[str, int]] = [(host_root + "/llms.txt", 0)]
    if seed_parts.path == "/docs" or seed_parts.path.startswith("/docs/"):
        queue.insert(0, (host_root + "/docs/llms.txt", 0))
    visited: set[str] = set()
    while queue and len(visited) < 10:
        index_url, depth = queue.pop(0)
        index_url = normalize_url(index_url) or index_url
        if index_url in visited:
            continue
        visited.add(index_url)
        try:
            final_url, body, content_type = fetch_source(index_url, timeout)
            if "text/plain" not in content_type and "text/markdown" not in content_type:
                continue
            documents.add(normalize_url(final_url) or final_url)
            text = body.decode("utf-8", "replace")
            for raw_link in parse_links(text, "text/markdown"):
                canonical = canonicalize_doc_link(raw_link, final_url, target)
                if not canonical:
                    continue
                if is_llms_index_url(canonical):
                    if depth < 1:
                        queue.append((canonical, depth + 1))
                    continue
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


def official_link_graph(
    target: Target,
    timeout: float,
    max_depth: int,
    page_limit: int,
    rate: float,
    errors: list[dict[str, str]],
) -> tuple[dict[str, set[str]], int]:
    """Expand official same-host navigation when the site publishes no sitemap."""
    seed = canonicalize_doc_link(target.seed, target.seed, target)
    if not seed or max_depth < 1:
        return {}, 0
    queue: deque[tuple[str, int]] = deque([(seed, 0)])
    visited: set[str] = set()
    links: dict[str, set[str]] = {}
    limiter = HostRateLimiter(rate)
    while queue and len(visited) < page_limit:
        page_url, depth = queue.popleft()
        if page_url in visited:
            continue
        visited.add(page_url)
        limiter.wait()
        try:
            final_url, body, content_type = fetch_source(page_url, timeout)
        except Exception as exc:
            errors.append({"source": page_url, "error": f"{type(exc).__name__}: {exc}"})
            continue
        if depth >= max_depth:
            continue
        body_text = body.decode("utf-8", "replace")
        for raw_link in parse_links(body_text, content_type):
            normalized = canonicalize_doc_link(raw_link, final_url, target)
            if not normalized or static_asset(normalized, ""):
                continue
            links.setdefault(normalized, set()).add(normalize_url(final_url) or final_url)
            if depth + 1 < max_depth and normalized not in visited:
                queue.append((normalized, depth + 1))
    return links, len(visited)


def route_type(url: str, content_type: str = "") -> str:
    path = urlsplit(url).path.lower()
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type in API_MEDIA_TYPES and (
        re.search(r"/(?:api|graphql)(?:/|$)", path) or path.endswith("/graphql")
    ):
        return "api_endpoint"
    if media_type in PAGE_MEDIA_TYPES and not path.endswith((".txt", ".xml", ".json")):
        if API_DOC_PATH_RE.search(path):
            return "api_documentation"
        return "page"
    if media_type in API_MEDIA_TYPES:
        return "structured_data"
    return "other"


def is_route_candidate(url: str, content_type: str, classification: str) -> bool:
    path = urlsplit(url).path.lower()
    return (
        not static_asset(url, content_type)
        and classification != "structured_data"
        and not path.endswith((".txt", ".xml"))
    )


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
    targets: dict[str, Target], timeout: float, navigation_depth: int, navigation_page_limit: int, rate: float
) -> tuple[dict[str, dict[str, dict[str, Any]]], dict[str, dict[str, Any]]]:
    records_by_site: dict[str, dict[str, dict[str, Any]]] = {}
    source_counts: dict[str, dict[str, Any]] = {}
    for site_id, target in targets.items():
        records: dict[str, dict[str, Any]] = {}
        errors: list[dict[str, str]] = []
        sitemap_set, sitemap_docs = sitemap_urls(target, timeout, errors)
        llms_set, llms_docs = llms_index_urls(target, timeout, errors)
        seed_set, seed_doc = seed_links(target, timeout, errors)
        graph_set: dict[str, set[str]] = {}
        graph_pages = 0
        if not sitemap_set:
            graph_set, graph_pages = official_link_graph(
                target, timeout, navigation_depth, navigation_page_limit, rate, errors
            )
        for url, docs in sitemap_set.items():
            for source_document in docs:
                add_source(records, url, "sitemap", source_document, target)
        for url, docs in llms_set.items():
            for source_document in docs:
                add_source(records, url, "llms_index", source_document, target)
        for url in seed_set:
            add_source(records, url, "seed_navigation", seed_doc, target)
        for url, docs in graph_set.items():
            for source_document in docs:
                add_source(records, url, "official_navigation_graph", source_document, target)
        records_by_site[site_id] = records
        source_counts[site_id] = {
            "sitemap_documents": len(sitemap_docs),
            "sitemap_urls": len(sitemap_set),
            "llms_documents": len(llms_docs),
            "llms_urls": len(llms_set),
            "seed_links": max(0, len(seed_set) - 1),
            "navigation_graph_urls": len(graph_set),
            "navigation_pages_fetched": graph_pages,
            "navigation_page_limit": navigation_page_limit if not sitemap_set else 0,
            "navigation_page_cap_reached": bool(not sitemap_set and graph_pages >= navigation_page_limit),
            "unique_source_urls": len(records),
            "errors": errors,
        }
    return records_by_site, source_counts


def load_source_snapshot(
    source_dir: Path, targets: dict[str, Target]
) -> tuple[dict[str, dict[str, dict[str, Any]]], dict[str, dict[str, Any]]]:
    """Load a previously verified, fixed authoritative source set."""
    metadata_path = source_dir / "metadata.json"
    gold_path = source_dir / "goldset.csv"
    if not metadata_path.is_file() or not gold_path.is_file():
        raise ValueError(f"source snapshot needs metadata.json and goldset.csv: {source_dir}")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    snapshot_targets = metadata.get("targets", {})
    snapshot_counts = metadata.get("source_counts", {})
    records_by_site = {site_id: {} for site_id in targets}
    source_counts: dict[str, dict[str, Any]] = {}
    for site_id, target in targets.items():
        source_seed = snapshot_targets.get(site_id)
        if source_seed and normalize_url(source_seed) != normalize_url(target.seed):
            raise ValueError(f"source snapshot seed mismatch for {site_id}: {source_seed} != {target.seed}")
        if site_id not in snapshot_counts:
            raise ValueError(f"source snapshot is missing source counts for {site_id}")
        source_counts[site_id] = snapshot_counts[site_id]
    with gold_path.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            site_id = row.get("site_id", "")
            if site_id not in targets:
                continue
            url = normalize_url(row.get("url", ""))
            if not url:
                continue
            record = records_by_site[site_id].setdefault(
                url, {"url": url, "source_types": set(), "source_documents": set()}
            )
            record["source_types"].update(filter(None, row.get("source_types", "").split(";")))
            record["source_documents"].update(filter(None, row.get("source_documents", "").split(";")))
    return records_by_site, source_counts


def verify_all(
    urls: dict[str, set[str]],
    targets: dict[str, Target],
    timeout: float,
    workers_per_host: int,
    rate: float,
    cached: dict[tuple[str, str], FetchResult] | None = None,
    checkpoint_path: Path | None = None,
) -> dict[tuple[str, str], FetchResult]:
    work: list[tuple[str, str]] = [
        (site_id, url)
        for site_id, site_urls in urls.items()
        for url in sorted(site_urls)
        if not cached or (site_id, url) not in cached
    ]
    limiters = {
        site_id: HostRateLimiter(rate)
        for site_id in targets
    }
    results: dict[tuple[str, str], FetchResult] = dict(cached or {})
    print(f"HTTP checks queued: {len(work)}; cached: {len(results)}", flush=True)
    max_workers = max(1, workers_per_host * len(targets))
    checkpoint = None
    if checkpoint_path:
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        checkpoint = checkpoint_path.open("a", encoding="utf-8")
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
            if checkpoint:
                check = results[key]
                checkpoint.write(json.dumps({
                    "site_id": key[0], "url": key[1], "status": check.status,
                    "content_type": check.content_type, "final_url": check.final_url,
                    "method": check.method, "error": check.error,
                }, ensure_ascii=True) + "\n")
                checkpoint.flush()
            completed += 1
            if completed % 100 == 0 or completed == len(work):
                print(f"HTTP verification {completed}/{len(work)}", flush=True)
    if checkpoint:
        checkpoint.close()
    return results


def load_cached_checks(cache_dir: Path | None) -> dict[tuple[str, str], FetchResult]:
    if not cache_dir:
        return {}
    cache: dict[tuple[str, str], FetchResult] = {}
    for filename in ("goldset.csv", "candidate-verification.csv"):
        path = cache_dir / filename
        if not path.is_file():
            continue
        with path.open(encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                site_id, url = row.get("site_id", ""), normalize_url(row.get("url", ""))
                if not site_id or not url or (site_id, url) in cache:
                    continue
                status_raw = row.get("http_status", "")
                status = int(status_raw) if status_raw.isdigit() else None
                cache[(site_id, url)] = FetchResult(
                    status,
                    row.get("content_type", ""),
                    row.get("final_url", "") or url,
                    row.get("verify_method", "HEAD"),
                    row.get("error", ""),
                )
    progress_path = cache_dir / "http-check-progress.jsonl"
    if progress_path.is_file():
        for line in progress_path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
                site_id = row.get("site_id", "")
                url = normalize_url(row.get("url", ""))
                if site_id and url:
                    cache[(site_id, url)] = FetchResult(
                        row.get("status"), row.get("content_type", ""),
                        row.get("final_url", "") or url, row.get("method", "HEAD"),
                        row.get("error", ""),
                    )
            except json.JSONDecodeError:
                continue
    return cache


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
    navigation_depth: int,
    navigation_page_limit: int,
    reused_from: Path | None,
    source_snapshot_from: Path | None,
    reused_count: int,
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
            is_route = is_route_candidate(url, check.content_type, result_class)
            if is_route:
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
                "route_candidate": is_route,
                "in_goldset": url in records and url in valid_gold,
                "error": check.error,
            })

        tp = len(candidates & valid_gold)
        valid_gold_by_site[site_id] = valid_gold
        precision = tp / route_candidates if route_candidates else 0.0
        recall = tp / len(valid_gold) if valid_gold else 0.0
        site_gold_rows = [row for row in gold_rows if row["site_id"] == site_id and row["gold_valid"]]
        gold_pages = sum(row["route_type"] == "page" for row in site_gold_rows)
        gold_api_docs = sum(row["route_type"] == "api_documentation" for row in site_gold_rows)
        gold_api_endpoints = sum(row["route_type"] == "api_endpoint" for row in site_gold_rows)
        counts = source_counts[site_id]
        metrics_rows.append({
            "site_id": site_id,
            "sitemap_urls": counts["sitemap_urls"],
            "llms_urls": counts["llms_urls"],
            "seed_links": counts["seed_links"],
            "navigation_graph_urls": counts.get("navigation_graph_urls", 0),
            "navigation_pages_fetched": counts.get("navigation_pages_fetched", 0),
            "navigation_page_cap_reached": counts.get("navigation_page_cap_reached", False),
            "source_unique_urls": counts["unique_source_urls"],
            "verified_gold_urls": len(valid_gold),
            "verified_pages": gold_pages,
            "verified_api_documentation_pages": gold_api_docs,
            "verified_api_endpoints": gold_api_endpoints,
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
        "route_type", "static_asset", "route_candidate", "in_goldset", "error",
    ], candidate_rows)
    csv_write(output_dir / "metrics.csv", [
        "site_id", "sitemap_urls", "llms_urls", "seed_links", "navigation_graph_urls", "navigation_pages_fetched",
        "navigation_page_cap_reached", "source_unique_urls",
        "verified_gold_urls", "verified_pages", "verified_api_documentation_pages", "verified_api_endpoints",
        "candidate_urls", "route_candidates_ex_assets",
        "http_accessible_route_candidates", "true_positives", "gold_precision", "gold_recall",
    ], metrics_rows)

    report = [
        "# Verified crawler URL gold set", "",
        f"Run directory: `{run_dir}`", f"Started: `{started_at.isoformat()}`", "",
        "The authoritative set combines same-host URLs from `robots.txt`/sitemap documents, the official `llms.txt` documentation index when available, and links on the configured seed page. Every unique source URL and every unique in-scope basecrawler candidate is checked with HEAD, falling back to a one-byte ranged GET when HEAD is rejected.", "",
        "A URL is counted as a verified gold page/API record only when its response is 2xx/3xx and its content type is HTML, Markdown, XHTML, or plain text on a route without a file extension. A JSON response is counted as an API endpoint only when its path is under `/api/` or `/graphql`. API reference documentation pages are kept as documentation pages, not mislabeled as live API endpoints.", "",
        "`gold_precision` is source-backed precision: verified source URLs that also appear in basecrawler candidates divided by route candidates after excluding static assets and structured-data files. `gold_recall` is the share of all verified source-backed gold pages/API records present in the candidate set. Sources can omit valid but unlisted URLs, so these metrics describe this explicit source-defined gold set, not every possible route on a host.", "",
        f"Verification timeout: {timeout:g}s; maximum start rate: {rate:g} requests/second per site; workers per site: {workers_per_host}.", "",
        f"Official navigation crawl depth: {navigation_depth}; page cap per sitemap-less host: {navigation_page_limit}; reused prior HTTP checks: {reused_count} from `{reused_from or ''}`.", "",
        f"Authoritative source snapshot reused: `{source_snapshot_from}`." if source_snapshot_from else "Authoritative source URLs collected live for this run.", "",
        f"Unique URL status checks: {len(checks)} total, {max(0, len(checks) - reused_count)} new this pass, {reused_count} reused.", "",
        "| Site | Sitemap | Index | Seed links | Expanded nav URLs | Nav pages read | Cap reached | Gold pages | API docs | Live APIs | Route candidates | TP | HTTP yield | Precision | Recall |",
        "|---|---:|---:|---:|---:|---:|:---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in metrics_rows:
        report.append(
            f"| {row['site_id']} | {row['sitemap_urls']} | {row['llms_urls']} | {row['seed_links']} | {row['navigation_graph_urls']} | "
            f"{row['navigation_pages_fetched']} | {'yes' if row['navigation_page_cap_reached'] else 'no'} | "
            f"{row['verified_pages']} | {row['verified_api_documentation_pages']} | {row['verified_api_endpoints']} | "
            f"{row['route_candidates_ex_assets']} | {row['true_positives']} | "
            f"{row['http_accessible_route_candidates'] / row['route_candidates_ex_assets']:.1%} | "
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
        "navigation_depth": navigation_depth,
        "navigation_page_limit": navigation_page_limit,
        "reused_checks_from": str(reused_from) if reused_from else "",
        "reused_sources_from": str(source_snapshot_from) if source_snapshot_from else "",
        "reused_check_count": reused_count,
        "unique_http_check_count": len(checks),
        "new_http_check_count": max(0, len(checks) - reused_count),
        "source_counts": source_counts,
        "source_policy": [
            "robots_sitemap", "root_sitemap_fallback", "official_llms_index",
            "seed_page_links", "bounded_official_link_graph_when_no_sitemap",
        ],
    }
    (output_dir / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")


def reclassify_existing(output_dir: Path) -> int:
    """Recompute route metrics from saved HTTP responses without re-requesting sites."""
    metadata_path = output_dir / "metadata.json"
    if not metadata_path.is_file():
        raise ValueError(f"gold-set output has no metadata.json: {output_dir}")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    gold_path = output_dir / "goldset.csv"
    candidate_path = output_dir / "candidate-verification.csv"
    with gold_path.open(encoding="utf-8-sig", newline="") as stream:
        gold_rows = list(csv.DictReader(stream))
    with candidate_path.open(encoding="utf-8-sig", newline="") as stream:
        candidate_rows = list(csv.DictReader(stream))

    sites = list(metadata["targets"])
    valid_gold: dict[str, set[str]] = {site: set() for site in sites}
    for row in gold_rows:
        classification = route_type(row["url"], row.get("content_type", ""))
        status = int(row["http_status"]) if row.get("http_status", "").isdigit() else None
        is_gold = bool(status is not None and 200 <= status < 400 and classification in {"page", "api_documentation", "api_endpoint"})
        row["route_type"] = classification
        row["gold_valid"] = is_gold
        if is_gold:
            valid_gold[row["site_id"]].add(row["url"])
    for row in candidate_rows:
        classification = route_type(row["url"], row.get("content_type", ""))
        status = int(row["http_status"]) if row.get("http_status", "").isdigit() else None
        is_asset = static_asset(row["url"], row.get("content_type", ""))
        is_route = is_route_candidate(row["url"], row.get("content_type", ""), classification)
        row["route_type"] = classification
        row["static_asset"] = is_asset
        row["route_candidate"] = is_route
        row["in_goldset"] = row["url"] in valid_gold[row["site_id"]]

    csv_write(gold_path, [
        "site_id", "url", "source_types", "source_documents", "route_type",
        "http_status", "content_type", "final_url", "verify_method", "error", "gold_valid",
    ], gold_rows)
    csv_write(candidate_path, [
        "site_id", "url", "http_status", "content_type", "final_url", "verify_method",
        "route_type", "static_asset", "route_candidate", "in_goldset", "error",
    ], candidate_rows)

    source_counts = metadata["source_counts"]
    metrics_rows: list[dict[str, Any]] = []
    for site_id in sites:
        candidates = {row["url"] for row in candidate_rows if row["site_id"] == site_id}
        site_candidates = [row for row in candidate_rows if row["site_id"] == site_id]
        route_candidates = sum(row.get("route_candidate") is True for row in site_candidates)
        accessible_routes = sum(
            row.get("route_candidate") is True
            and row.get("http_status", "").isdigit()
            and 200 <= int(row["http_status"]) < 400
            and row.get("route_type") in {"page", "api_documentation", "api_endpoint"}
            for row in site_candidates
        )
        tp = len(candidates & valid_gold[site_id])
        denominator = len(valid_gold[site_id])
        counts = source_counts[site_id]
        metrics_rows.append({
            "site_id": site_id,
            "sitemap_urls": counts["sitemap_urls"],
            "llms_urls": counts["llms_urls"],
            "seed_links": counts["seed_links"],
            "navigation_graph_urls": counts.get("navigation_graph_urls", 0),
            "navigation_pages_fetched": counts.get("navigation_pages_fetched", 0),
            "navigation_page_cap_reached": counts.get("navigation_page_cap_reached", False),
            "source_unique_urls": counts["unique_source_urls"],
            "verified_gold_urls": denominator,
            "verified_pages": sum(row["gold_valid"] and row["route_type"] == "page" for row in gold_rows if row["site_id"] == site_id),
            "verified_api_documentation_pages": sum(row["gold_valid"] and row["route_type"] == "api_documentation" for row in gold_rows if row["site_id"] == site_id),
            "verified_api_endpoints": sum(row["gold_valid"] and row["route_type"] == "api_endpoint" for row in gold_rows if row["site_id"] == site_id),
            "candidate_urls": len(candidates),
            "route_candidates_ex_assets": route_candidates,
            "http_accessible_route_candidates": accessible_routes,
            "true_positives": tp,
            "gold_precision": f"{tp / route_candidates:.4f}" if route_candidates else "0.0000",
            "gold_recall": f"{tp / denominator:.4f}" if denominator else "n/a",
        })
    metrics_path = output_dir / "metrics.csv"
    csv_write(metrics_path, [
        "site_id", "sitemap_urls", "llms_urls", "seed_links", "navigation_graph_urls", "navigation_pages_fetched",
        "navigation_page_cap_reached", "source_unique_urls",
        "verified_gold_urls", "verified_pages", "verified_api_documentation_pages", "verified_api_endpoints",
        "candidate_urls", "route_candidates_ex_assets",
        "http_accessible_route_candidates", "true_positives", "gold_precision", "gold_recall",
    ], metrics_rows)

    run_dir = metadata.get("run_dir", "")
    started = metadata.get("started_at", "")
    report = [
        "# Verified crawler URL gold set", "", f"Run directory: `{run_dir}`", f"Started: `{started}`", "",
        "The authoritative set combines same-host URLs from `robots.txt`/sitemap documents, the official `llms.txt` documentation index when available, and links on the configured seed page. Every unique source URL and every unique in-scope basecrawler candidate was checked with HEAD, falling back to a one-byte ranged GET when HEAD was rejected.", "",
        "A URL is counted as a verified gold page/API record only when its response is 2xx/3xx and its content type is HTML, Markdown, XHTML, or plain text on a route without a file extension. A JSON response is counted as an API endpoint only when its path is under `/api/` or `/graphql`. API reference documentation pages are kept as documentation pages, not mislabeled as live API endpoints.", "",
        "`gold_precision` is source-backed precision: verified source URLs that also appear in basecrawler candidates divided by route candidates after excluding static assets and structured-data files. `gold_recall` is the share of all verified source-backed gold pages/API records present in the candidate set. Sources can omit valid but unlisted URLs, so these metrics describe this explicit source-defined gold set, not every possible route on a host.", "",
        f"Verification timeout: {metadata['timeout_seconds']:g}s; maximum start rate: {metadata['max_requests_per_second_per_site']:g} requests/second per site; workers per site: {metadata['workers_per_site']}.", "",
        f"Official navigation crawl depth: {metadata.get('navigation_depth', 1)}; page cap per sitemap-less host: {metadata.get('navigation_page_limit', 0)}; reused prior HTTP checks: {metadata.get('reused_check_count', 0)}.", "",
        "| Site | Sitemap | Index | Seed links | Expanded nav URLs | Nav pages read | Cap reached | Gold pages | API docs | Live APIs | Route candidates | TP | HTTP yield | Precision | Recall |",
        "|---|---:|---:|---:|---:|---:|:---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in metrics_rows:
        recall = row["gold_recall"] if row["gold_recall"] == "n/a" else f"{float(row['gold_recall']):.1%}"
        report.append(
            f"| {row['site_id']} | {row['sitemap_urls']} | {row['llms_urls']} | {row['seed_links']} | {row['navigation_graph_urls']} | "
            f"{row['navigation_pages_fetched']} | {'yes' if row['navigation_page_cap_reached'] else 'no'} | "
            f"{row['verified_pages']} | {row['verified_api_documentation_pages']} | {row['verified_api_endpoints']} | "
            f"{row['route_candidates_ex_assets']} | {row['true_positives']} | "
            f"{row['http_accessible_route_candidates'] / row['route_candidates_ex_assets']:.1%} | "
            f"{float(row['gold_precision']):.1%} | {recall} |"
        )
    unique_check_count = len({
        (row.get("site_id", ""), row.get("url", ""))
        for row in [*gold_rows, *candidate_rows]
        if row.get("site_id") and row.get("url")
    })
    reused_count = int(metadata.get("reused_check_count", 0))
    metadata["unique_http_check_count"] = unique_check_count
    metadata["new_http_check_count"] = max(0, unique_check_count - reused_count)
    report.extend([
        f"Unique URL status checks: {unique_check_count} total, {metadata['new_http_check_count']} new this pass, {reused_count} reused.", "",
        "Source collection errors are listed in `metadata.json`. Full URL-level provenance and HTTP results are in `goldset.csv`; candidate checks are in `candidate-verification.csv`.",
    ])
    (output_dir / "summary.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    metadata["reclassified_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(f"reclassified saved HTTP results: {len(gold_rows) + len(candidate_rows)} URLs")
    return 0


def self_test() -> int:
    target = TARGETS["framework-react"]
    assert parse_links("- [Quick Start](https://react.dev/learn.md)\n", "text/markdown") == ["https://react.dev/learn.md"]
    assert canonicalize_doc_link("https://react.dev/learn.md", "https://react.dev/llms.txt", target) == "https://react.dev/learn"
    assert canonicalize_doc_link("/other", "https://evil.example/docs", target) is None
    assert is_llms_index_url("https://react.dev/docs/kit/llms.txt")
    assert not is_llms_index_url("https://react.dev/docs/llms-full.txt")
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
    assert route_type("https://react.dev/learn", "text/plain") == "page"
    assert route_type("https://x.test/api/items", "application/json") == "api_endpoint"
    assert static_asset("https://x.test/app.js", "text/javascript")
    with tempfile.TemporaryDirectory() as temp_dir:
        progress = Path(temp_dir) / "http-check-progress.jsonl"
        progress.write_text(json.dumps({
            "site_id": "framework-react", "url": "https://react.dev/learn",
            "status": 200, "content_type": "text/plain", "final_url": "https://react.dev/learn",
            "method": "HEAD", "error": "",
        }) + "\n", encoding="utf-8")
        cached = load_cached_checks(Path(temp_dir))
        assert cached[("framework-react", "https://react.dev/learn")].status == 200
        snapshot = Path(temp_dir) / "snapshot"
        snapshot.mkdir()
        (snapshot / "metadata.json").write_text(json.dumps({
            "targets": {"framework-react": target.seed},
            "source_counts": {"framework-react": {"unique_source_urls": 1}},
        }), encoding="utf-8")
        (snapshot / "goldset.csv").write_text(
            "site_id,url,source_types,source_documents\n"
            "framework-react,https://react.dev/learn,llms_index,https://react.dev/llms.txt\n",
            encoding="utf-8",
        )
        records, counts = load_source_snapshot(snapshot, {"framework-react": target})
        assert set(records["framework-react"]) == {"https://react.dev/learn"}
        assert records["framework-react"]["https://react.dev/learn"]["source_types"] == {"llms_index"}
        assert counts["framework-react"]["unique_source_urls"] == 1
    print("self-test passed")
    return 0


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build an HTTP-verified crawler gold set from authoritative site sources.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("self-test", help="run offline parser and classification checks")
    reclassify = sub.add_parser("reclassify", help="recompute route metrics from saved HTTP checks without network requests")
    reclassify.add_argument("--output-dir", type=Path, required=True)
    build = sub.add_parser("build", help="collect and verify official URLs and basecrawler candidates")
    build.add_argument("--run-dir", type=Path, required=True, help="crawler comparison run directory with urls.csv")
    build.add_argument("--output-dir", type=Path, help="output directory for goldset and metrics")
    build.add_argument("--targets", default=DEFAULT_TARGETS, help="comma-separated site IDs")
    build.add_argument("--timeout", type=float, default=12.0)
    build.add_argument("--workers-per-site", type=int, default=4)
    build.add_argument("--rate", type=float, default=2.0, help="maximum request starts per second per site")
    build.add_argument("--navigation-depth", type=int, default=2, help="link depth for sitemap-less hosts")
    build.add_argument("--navigation-page-limit", type=int, default=500, help="maximum official pages fetched per sitemap-less host")
    build.add_argument("--reuse-checks-from", type=Path, help="reuse saved HTTP checks for overlapping URLs")
    build.add_argument("--reuse-sources-from", type=Path, help="reuse the fixed source URL set from a previous gold-set output")
    build.add_argument("--dry-run", action="store_true", help="collect sources and print counts without HTTP verification")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = make_parser()
    args = parser.parse_args(argv)
    if args.command == "self-test":
        return self_test()
    if args.command == "reclassify":
        try:
            return reclassify_existing(args.output_dir)
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            parser.error(str(exc))
    if args.workers_per_site < 1 or args.rate <= 0 or args.timeout <= 0 or args.navigation_depth < 1 or args.navigation_page_limit < 1:
        parser.error("workers, rate, timeout, navigation depth, and page limit must be positive")
    site_ids = [part.strip() for part in args.targets.split(",") if part.strip()]
    unknown = sorted(set(site_ids) - set(TARGETS))
    if unknown:
        parser.error(f"unknown target(s): {', '.join(unknown)}")
    targets = {site_id: TARGETS[site_id] for site_id in site_ids}
    started_at = dt.datetime.now(dt.timezone.utc)
    source_snapshot_from = args.reuse_sources_from
    if source_snapshot_from:
        if args.output_dir and args.output_dir.resolve() == source_snapshot_from.resolve():
            parser.error("output directory must differ from --reuse-sources-from")
        try:
            records_by_site, source_counts = load_source_snapshot(source_snapshot_from, targets)
        except (OSError, ValueError, KeyError, json.JSONDecodeError, csv.Error) as exc:
            parser.error(str(exc))
    else:
        records_by_site, source_counts = collect_sources(
            targets, args.timeout, args.navigation_depth, args.navigation_page_limit, args.rate
        )
    candidates_by_site = load_candidates(args.run_dir, set(site_ids))
    all_urls = {
        site_id: set(records_by_site[site_id]) | candidates_by_site[site_id]
        for site_id in site_ids
    }
    print("site_id\tsitemap\tllms\tseed_links\tnav_graph\tunique_sources\tcandidates\tchecks")
    for site_id in site_ids:
        counts = source_counts[site_id]
        print(
            f"{site_id}\t{counts['sitemap_urls']}\t{counts['llms_urls']}\t{counts['seed_links']}\t{counts['navigation_graph_urls']}\t"
            f"{counts['unique_source_urls']}\t{len(candidates_by_site[site_id])}\t{len(all_urls[site_id])}"
        )
    if args.dry_run:
        return 0
    output_dir = args.output_dir or DEFAULT_OUTPUT_ROOT / f"verified-goldset-{started_at:%Y%m%d}"
    checks_from = args.reuse_checks_from or source_snapshot_from
    cached = load_cached_checks(checks_from)
    cached = {
        key: value for key, value in cached.items()
        if key[0] in all_urls and key[1] in all_urls[key[0]]
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = output_dir / "http-check-progress.jsonl"
    checks = verify_all(
        all_urls, targets, args.timeout, args.workers_per_site, args.rate, cached, checkpoint_path
    )
    build_report(
        output_dir, args.run_dir, targets, records_by_site, candidates_by_site,
        source_counts, checks, started_at, args.timeout, args.workers_per_site, args.rate,
        args.navigation_depth, args.navigation_page_limit,
        checks_from, source_snapshot_from, len(cached),
    )
    checkpoint_path.unlink(missing_ok=True)
    print(f"summary: {output_dir / 'summary.md'}")
    print(f"goldset: {output_dir / 'goldset.csv'}")
    print(f"metrics: {output_dir / 'metrics.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
