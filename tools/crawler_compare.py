#!/usr/bin/env python3
"""Run repeatable, low-rate crawler comparisons and emit per-site reports.

This tool uses only the Python standard library. `plan` is network-free;
`run` invokes the configured crawler executables sequentially.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit


ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = ROOT / "othercrawlers"
DEFAULT_OUTPUT_ROOT = ROOT / "artifacts" / "crawler-comparison"
ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
URL_RE = re.compile(r"https?://[^\s\"'<>`\[\]{}]+", re.IGNORECASE)
TRAILING_URL_PUNCTUATION = ".,;:!?)]}"


@dataclass(frozen=True)
class Target:
    site_id: str
    seed: str
    profile: str
    allowed_hosts: tuple[str, ...]
    depth: int
    max_pages: int
    concurrency: int
    timeout: int
    host_rate: float
    optional: bool = False
    max_response_bytes: int = 2_097_152
    proxy: str = ""
    disable_bootstrap: bool = False


def _target(
    site_id: str,
    seed: str,
    profile: str,
    *,
    depth: int = 3,
    max_pages: int = 500,
    concurrency: int = 4,
    timeout: int = 10,
    host_rate: float = 2,
    optional: bool = False,
    max_response_bytes: int = 2_097_152,
    proxy: str = "",
    disable_bootstrap: bool = False,
) -> Target:
    host = (urlsplit(seed).hostname or "").lower()
    return Target(
        site_id, seed, profile, (host,), depth, max_pages, concurrency,
        timeout, host_rate, optional, max_response_bytes, proxy, disable_bootstrap
    )


TARGETS: dict[str, Target] = {
    "testasp": _target("testasp", "http://testasp.vulnweb.com/", "traditional"),
    "testphp": _target("testphp", "http://testphp.vulnweb.com/", "traditional"),
    "testaspnet": _target("testaspnet", "http://testaspnet.vulnweb.com/", "traditional"),
    "testhtml5": _target("testhtml5", "http://testhtml5.vulnweb.com/", "frontend"),
    "juice-shop": _target(
        "juice-shop", "https://demo.owasp-juice.shop/", "angular-spa",
        depth=2, max_pages=100, concurrency=1, timeout=15, host_rate=1,
        optional=True,
    ),
    "framework-vue": _target(
        "framework-vue", "https://vuejs.org/guide/introduction.html", "framework-vue",
        depth=1, max_pages=20, concurrency=1, timeout=15, host_rate=1,
    ),
    "framework-react": _target(
        "framework-react", "https://react.dev/learn", "framework-react",
        depth=1, max_pages=20, concurrency=1, timeout=15, host_rate=1,
    ),
    "framework-angular": _target(
        "framework-angular", "https://angular.dev/tutorials/first-app", "framework-angular",
        depth=1, max_pages=20, concurrency=1, timeout=15, host_rate=1,
    ),
    "framework-nuxt": _target(
        "framework-nuxt", "https://learn.nuxt.com/en", "framework-nuxt",
        depth=1, max_pages=20, concurrency=1, timeout=15, host_rate=1,
    ),
    "framework-nextjs": _target(
        "framework-nextjs", "https://nextjs.org/docs", "framework-nextjs",
        depth=1, max_pages=20, concurrency=1, timeout=15, host_rate=1,
    ),
    "framework-sveltekit": _target(
        "framework-sveltekit", "https://svelte.dev/docs/kit/introduction", "framework-sveltekit",
        depth=1, max_pages=20, concurrency=1, timeout=15, host_rate=1,
    ),
}


@dataclass(frozen=True)
class Tool:
    name: str
    mode: str
    binary: str
    version_args: tuple[str, ...]
    supported: bool = True


TOOLS: dict[str, Tool] = {
    "basecrawler": Tool("basecrawler", "static", "", ("--version",)),
    "browcrawler": Tool("browcrawler", "browser", "", ("--version",)),
    "katana": Tool("katana", "static", "katana.exe", ("-version",)),
    "hakrawler": Tool("hakrawler", "static", "hakrawler.exe", ("-version",)),
    "gospider": Tool("gospider", "static", "gospider.exe", ("-version",)),
    "cariddi": Tool("cariddi", "static", "cariddi.exe", ("--version",)),
    "urlfinder": Tool("urlfinder", "static", "URLFinder.exe", ("-h",)),
    "crawlergo": Tool("crawlergo", "browser", "crawlergo.exe", ("--version",)),
    "argo": Tool("argo", "browser", "argo.exe", ("--version",)),
    "jsowl": Tool("jsowl", "js-analysis", "jsowl.exe", ("--version",)),
    "jshunter": Tool("jshunter", "js-analysis", "jshunter.exe", ("--help",)),
}


def normalize_url(raw_url: str) -> str | None:
    """Canonicalize only syntax that is safe to normalize across tools."""
    candidate = re.sub(r":\d+:\d+(?=[?#]|$)", "", raw_url.strip())
    candidate = candidate.rstrip(TRAILING_URL_PUNCTUATION)
    try:
        parts = urlsplit(candidate)
        if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
            return None
        scheme = parts.scheme.lower()
        host = parts.hostname.lower()
        try:
            port = parts.port
        except ValueError:
            return None
        if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
            host = f"{host}:{port}"
        if ":" in parts.hostname and not parts.hostname.startswith("["):
            host = f"[{parts.hostname.lower()}]" + (f":{port}" if port else "")
        path = parts.path or "/"
        segments: list[str] = []
        for segment in path.split("/"):
            if segment == "..":
                if segments:
                    segments.pop()
            elif segment != ".":
                segments.append(segment)
        path = "/".join(segments) or "/"
        if not path.startswith("/"):
            path = "/" + path
        return urlunsplit((scheme, host, path, parts.query, ""))
    except (ValueError, UnicodeError):
        return None


def in_scope(url: str, target: Target) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return any(
        host == allowed.lower() if not allowed.startswith("*.")
        else host.endswith("." + allowed[2:].lower())
        for allowed in target.allowed_hosts
    )


def _json_urls(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        url_keys = {"url", "uri", "href", "link", "endpoint", "target", "request_url", "response_url", "final_url", "source_url"}
        ignored = {"body", "content", "html", "script", "source", "raw", "request_body", "response_body"}
        for key, item in value.items():
            normalized_key = str(key).lower().replace("-", "_")
            if normalized_key in url_keys and isinstance(item, str):
                yield from URL_RE.findall(item)
            elif normalized_key not in ignored and isinstance(item, (dict, list)):
                yield from _json_urls(item)
    elif isinstance(value, list):
        for item in value:
            yield from _json_urls(item)


def extract_urls(text: str) -> set[str]:
    clean = ANSI_RE.sub("", text)
    found: set[str] = set()
    try:
        parsed = json.loads(clean)
    except json.JSONDecodeError:
        parsed = None
    if parsed is not None:
        found.update(_json_urls(parsed))
    else:
        for line in clean.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith(("{", "[")):
                try:
                    found.update(_json_urls(json.loads(stripped)))
                    continue
                except json.JSONDecodeError:
                    pass
            found.update(URL_RE.findall(stripped))
    normalized = {re.sub(r":\d+:\d+(?=[?#]|$)", "", url).rstrip(TRAILING_URL_PUNCTUATION) for url in found}
    return {url for url in normalized if normalize_url(url)}


def target_from_json(item: dict[str, Any]) -> Target:
    required = {"site_id", "seed", "allowed_hosts"}
    missing = required - item.keys()
    if missing:
        raise ValueError(f"target missing fields: {', '.join(sorted(missing))}")
    seed = str(item["seed"])
    if not normalize_url(seed):
        raise ValueError(f"invalid seed URL for {item['site_id']}: {seed}")
    raw_hosts = item["allowed_hosts"]
    if isinstance(raw_hosts, str):
        raw_hosts = [raw_hosts]
    if not isinstance(raw_hosts, list):
        raise ValueError(f"target {item['site_id']} allowed_hosts must be a string or array")
    hosts = tuple(str(h).lower().strip() for h in raw_hosts if str(h).strip())
    if not hosts:
        raise ValueError(f"target {item['site_id']} must define allowed_hosts")
    return Target(
        site_id=str(item["site_id"]),
        seed=seed,
        profile=str(item.get("profile", "custom")),
        allowed_hosts=hosts,
        depth=int(item.get("depth", 3)),
        max_pages=int(item.get("max_pages", 500)),
        concurrency=int(item.get("concurrency", 10)),
        timeout=int(item.get("timeout", 10)),
        host_rate=float(item.get("host_rate", 1)),
        optional=bool(item.get("optional", False)),
        max_response_bytes=int(item.get("max_response_bytes", 2_097_152)),
        proxy=str(item.get("proxy", "")),
        disable_bootstrap=bool(item.get("disable_bootstrap", False)),
    )


def load_targets(path: Path | None) -> dict[str, Target]:
    if path is None:
        return TARGETS.copy()
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get("targets") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise ValueError("targets file must be a JSON array or an object with a 'targets' array")
    result: dict[str, Target] = {}
    for item in rows:
        target = target_from_json(item)
        if target.site_id in result:
            raise ValueError(f"duplicate target site_id: {target.site_id}")
        result[target.site_id] = target
    return result


def resolve_tool_binary(name: str) -> Path | None:
    if name == "basecrawler":
        candidates = [
            ROOT / "basecrawler" / "bin" / "basecrawler.exe",
            ROOT / "basecrawler" / "basecrawler.exe",
            ROOT / "basecrawler" / "assetcrawl.exe",
            ROOT / "basecrawler" / "ac.exe",
        ]
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        for command in ("basecrawler.exe", "assetcrawl.exe", "ac.exe", "basecrawler", "ac"):
            found = shutil.which(command)
            if found:
                return Path(found)
        return None
    if name == "browcrawler":
        candidate = ROOT / "browcrawler" / "bin" / "crawler.exe"
        return candidate if candidate.is_file() else None
    path = TOOLS_DIR / TOOLS[name].binary
    return path if path.is_file() else None


def browcrawler_static_budget(target: Target) -> int:
    """Keep the integrated static pass within the comparison's page budget."""
    return target.max_pages if target.max_pages > 0 else 200


def tool_depth(name: str, target: Target) -> int:
    """Map depth=0 to each tool's unlimited convention or a high sentinel."""
    if target.depth != 0:
        return target.depth
    if name in {"basecrawler", "browcrawler", "gospider"}:
        return 0  # These tools document zero as unlimited depth.
    return 9_999_999  # Some CLIs treat zero as stop-at-seed; page/time budgets still apply.


def find_chrome(override: Path | None) -> Path | None:
    if override:
        return override if override.is_file() else None
    for env_name in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        base = os.environ.get(env_name)
        if not base:
            continue
        for relative in (
            Path("Google/Chrome/Application/chrome.exe"),
            Path("Microsoft/Edge/Application/msedge.exe"),
        ):
            candidate = Path(base) / relative
            if candidate.is_file():
                return candidate
    for command in ("chrome.exe", "chrome", "msedge.exe"):
        found = shutil.which(command)
        if found:
            return Path(found)
    return None


def build_command(
    name: str,
    target: Target,
    run_dir: Path,
    *,
    chrome: Path | None = None,
    brow_config: Path | None = None,
    allow_form_submit: bool = False,
) -> tuple[list[str] | None, Path, str | None]:
    tool = TOOLS[name]
    binary = resolve_tool_binary(name)
    cwd = ROOT
    if name == "browcrawler":
        cwd = ROOT / "browcrawler"
        obscura = cwd / "third" / "obscura" / "obscura.exe"
        if not obscura.is_file():
            return None, cwd, f"missing bundled Obscura binary: {obscura}"
        executable = binary
        if executable:
            prefix = [str(executable)]
        else:
            go = shutil.which("go")
            if not go:
                return None, cwd, "missing browcrawler/bin/crawler.exe and Go is not on PATH"
            prefix = [go, "run", "./cmd/browcrawler"]
        config_source = brow_config or (cwd / "config.yaml")
        try:
            config_text = config_source.read_text(encoding="utf-8")
        except OSError as exc:
            return None, cwd, f"cannot read browcrawler config: {exc}"
        obscura_setting = f"obscura_bin: {json.dumps(str(obscura.resolve()))}"
        if re.search(r"(?m)^obscura_bin\s*:", config_text):
            config_text = re.sub(
                r"(?m)^obscura_bin\s*:.*$", lambda _match: obscura_setting, config_text
            )
        else:
            config_text += "\n" + obscura_setting + "\n"
        if target.proxy:
            upstream_setting = f"upstream_proxy: {json.dumps(target.proxy)}"
            if re.search(r"(?m)^upstream_proxy\s*:", config_text):
                config_text = re.sub(
                    r"(?m)^upstream_proxy\s*:.*$", lambda _match: upstream_setting, config_text
                )
            else:
                config_text += "\n" + upstream_setting + "\n"
        if target.disable_bootstrap and re.search(r"(?m)^obey_robots\s*:", config_text):
            config_text = re.sub(r"(?m)^obey_robots\s*:.*$", "obey_robots: false", config_text)
        if target.max_response_bytes > 0 and re.search(r"(?m)^max_body_size\s*:", config_text):
            config_text = re.sub(
                r"(?m)^max_body_size\s*:.*$",
                f"max_body_size: {target.max_response_bytes}", config_text,
            )
        config = run_dir / "browcrawler-config.yaml"
        config.write_text(config_text, encoding="utf-8")
        discovered = run_dir / "discovered.jsonl"
        command = prefix + [
            "--config", str(config), "--url", target.seed,
            "--scope-mode", "fqdn", "--depth", str(target.depth),
            "--max-pages", str(target.max_pages), "--host-rate", str(target.host_rate),
            "--basecrawler-max-urls", str(browcrawler_static_budget(target)),
            "--interactions", "0", "--no-forms",
            "--backend", "obscura", "--concurrency", "1",
            "--discovered", str(discovered),
        ]
        if not target.disable_bootstrap:
            command.append("--obey-robots")
        return command, cwd, None

    if name == "basecrawler" and binary is None:
        go = shutil.which("go")
        if not go:
            return None, ROOT / "basecrawler", "missing basecrawler executable and Go is not on PATH"
        exe_prefix = [go, "run", "./cmd/basecrawler"]
        cwd = ROOT / "basecrawler"
    else:
        exe_prefix = [str(binary)]

    if not binary and name != "basecrawler":
        return None, cwd, f"missing executable: {tool.binary or 'basecrawler executable'}"
    exe = exe_prefix[0]
    host = (urlsplit(target.seed).hostname or "").lower()
    output = run_dir / "tool-output.jsonl"

    if name == "basecrawler":
        report = run_dir / "scan-report.json"
        discoveries = run_dir / "discoveries.jsonl"
        command = exe_prefix + [
            "-u", target.seed, "-d", str(tool_depth(name, target)), "-c", str(target.concurrency),
            "--timeout", str(target.timeout), "--max-urls", str(target.max_pages),
            "--max-body", str(target.max_response_bytes),
            "--scope", "fqdn", "--rl", str(max(1, int(target.host_rate))),
            "--phc", str(max(1, min(target.concurrency, 4))),
            "--no-fuzz", "--no-testfill", "--no-unsafe-send",
            "--report", str(report),
            "--discoveries", str(discoveries),
            "-O", "json", "-o", str(output),
        ]
        if target.disable_bootstrap:
            command.extend(["--no-robots", "--no-sitemap", "--no-llms", "--no-probe-openapi"])
    elif name == "katana":
        command = [exe, "-u", target.seed, "-d", str(tool_depth(name, target)), "-c", str(target.concurrency),
                   "-timeout", str(target.timeout), "-rl", str(max(1, int(target.host_rate))),
                   "-hrl", str(max(1, int(target.host_rate))), "-fs", "fqdn", "-jc", "-j",
                   "-ob", "-or", "-ct", "60s", "-silent", "-duc", "-o", str(output)]
    elif name == "hakrawler":
        command = [exe, "-d", str(tool_depth(name, target)), "-t", str(target.concurrency),
                   "-timeout", str(target.timeout), "-u", "-s"]
    elif name == "gospider":
        command = [exe, "-s", target.seed, "-d", str(tool_depth(name, target)), "-c", str(target.concurrency),
                   "-m", str(target.max_pages)]
    elif name == "cariddi":
        command = [exe, "-md", str(tool_depth(name, target)), "-c", str(target.concurrency),
                   "-t", str(target.timeout), "-plain"]
    elif name == "urlfinder":
        command = [exe, "-u", target.seed, "-d", host, "-m", "2", "-s", "all",
                   "-t", str(target.concurrency), "-time", str(target.timeout),
                   "-max", str(target.max_pages)]
    elif name == "crawlergo":
        if not allow_form_submit:
            return None, cwd, "crawlergo auto-fills/submits forms; pass --allow-form-submit only for an authorized test instance"
        if not chrome:
            return None, cwd, "Chrome/Edge not found; set --chrome"
        command = [exe, "--chromium-path", str(chrome), "--max-crawled-count", str(target.max_pages),
                   "--max-tab-count", str(max(1, min(target.concurrency, 4))),
                   "--output-mode", "json", "--output-json", str(output), "--fuzz-path=false",
                   target.seed]
    elif name == "argo":
        if not chrome:
            return None, cwd, "Chrome/Edge not found; set --chrome"
        command = [exe, "--chrome", str(chrome), "--engine", "hybrid", "--maxdepth", str(tool_depth(name, target)),
                   "--hostrate", str(max(1, int(target.host_rate))), "--rate", str(max(1, int(target.host_rate))),
                   "--browsertimeout", "120", "--tabtimeout", str(target.timeout),
                   "--tabcount", str(max(1, min(target.concurrency, 4))), "--fuzz=false",
                   "--scope", f"^https?://{re.escape(host)}(:[0-9]+)?/",
                   "--outputdir", str(run_dir), "--format", "json", "--target", target.seed]
    elif name == "jsowl":
        command = [exe, "--mode", "direct", "--scope", host, "--threads", str(target.concurrency),
                   "--output", str(run_dir), "--url", target.seed]
    elif name == "jshunter":
        output = run_dir / "jshunter.json"
        command = [exe, "--url", target.seed, "--crawl", str(tool_depth(name, target)), "--domain", host,
                   "--threads", str(target.concurrency), "--rate-limit",
                   str(max(1, int(1000 / max(target.host_rate, 0.1)))),
                    "--max-bytes", str(target.max_response_bytes), "--json", "--output", str(output)]
    else:
        return None, cwd, f"no command adapter implemented for {name}"

    if target.proxy:
        proxy_flags = {
            "basecrawler": ["--proxy", target.proxy],
            "katana": ["-proxy", target.proxy],
            "hakrawler": ["-proxy", target.proxy],
            "gospider": ["--proxy", target.proxy],
            "cariddi": ["-proxy", target.proxy],
            "urlfinder": ["-x", target.proxy],
            "crawlergo": ["--request-proxy", target.proxy],
            "argo": ["--proxy", target.proxy],
        }.get(name)
        if name == "jshunter":
            parsed_proxy = urlsplit(target.proxy if "://" in target.proxy else "http://" + target.proxy)
            proxy_flags = ["--proxy", parsed_proxy.netloc]
        if proxy_flags:
            if name == "crawlergo":
                command[-1:-1] = proxy_flags
            else:
                command.extend(proxy_flags)
    return command, cwd, None


def get_version(name: str) -> str:
    binary = resolve_tool_binary(name)
    if name in {"basecrawler", "browcrawler"} and binary is None and shutil.which("go"):
        return "source (go run)"
    if binary is None:
        return "missing"
    args = list(TOOLS[name].version_args)
    try:
        completed = subprocess.run(
            [str(binary), *args], cwd=ROOT, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=8,
            check=False, encoding="utf-8", errors="replace",
        )
        for line in completed.stdout.splitlines():
            line = ANSI_RE.sub("", line).strip()
            version_match = re.search(r"(?i)\bv?\d+\.\d+(?:\.\d+)?(?:[-+][\w.-]+)?\b", line)
            if version_match:
                return version_match.group(0)
            if line and not line.lower().startswith(("usage:", "flags:", "options:")):
                return line[:120]
    except (OSError, subprocess.TimeoutExpired):
        pass
    return "unknown"


def collect_output_urls(run_dir: Path, stdout: str, stderr: str) -> set[str]:
    texts = [stdout, stderr]
    for path in run_dir.rglob("*"):
        if not path.is_file() or path.name in {
            "stdout.txt", "stderr.txt", "command.json", "run-result.json", "urls.jsonl"
        }:
            continue
        if path.suffix.lower() not in {".json", ".jsonl", ".txt", ".csv", ".html", ".log"}:
            continue
        try:
            texts.append(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
    return set().union(*(extract_urls(text) for text in texts))


def collect_result_and_discovery_counts(run_dir: Path, stdout: str, stderr: str) -> tuple[int, int]:
    result_texts = [stdout, stderr]
    result_path = run_dir / "tool-output.jsonl"
    if result_path.is_file():
        result_texts.append(result_path.read_text(encoding="utf-8", errors="replace"))
    result_urls = set().union(*(extract_urls(text) for text in result_texts))

    discovery_urls: set[str] = set()
    for name in ("discoveries.jsonl", "discovered.jsonl"):
        path = run_dir / name
        if path.is_file():
            discovery_urls.update(extract_urls(path.read_text(encoding="utf-8", errors="replace")))
    return len(result_urls), len(discovery_urls)


def save_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_reports(output_dir: Path, run_rows: list[dict[str, Any]], url_rows: list[dict[str, Any]]) -> None:
    successful = {
        (row["site_id"], row["tool"], row["round"])
        for row in run_rows if row["status"] in {"completed", "recovered"}
    }
    grouped: dict[tuple[str, str, int], dict[str, set[str]]] = {}
    for row in url_rows:
        run_key = (row["site_id"], row["tool"], row["round"])
        if row["in_scope"] and run_key in successful:
            group_key = (row["site_id"], row["mode"], row["round"])
            grouped.setdefault(group_key, {}).setdefault(row["tool"], set()).add(row["normalized_url"])
    unique_only: dict[tuple[str, str, int], int] = {}
    for key, tools_for_site in grouped.items():
        if len(tools_for_site) < 2:
            continue
        for tool, urls in tools_for_site.items():
            peers = set().union(*(other for peer, other in tools_for_site.items() if peer != tool)) if len(tools_for_site) > 1 else set()
            unique_only[(key[0], tool, key[2])] = len(urls - peers)

    for row in run_rows:
        row["unique_only"] = unique_only.get((row["site_id"], row["tool"], row["round"]))
    save_csv(output_dir / "results.csv", run_rows, [
        "site_id", "profile", "tool", "mode", "round", "version", "status", "exit_code",
        "duration_seconds", "result_urls", "discovery_urls", "unique_urls", "in_scope_urls", "out_scope_urls", "unique_only",
        "command", "stdout_file", "stderr_file", "error",
    ])
    save_csv(output_dir / "urls.csv", url_rows, [
        "site_id", "tool", "mode", "round", "raw_url", "normalized_url", "in_scope",
    ])
    (output_dir / "run.json").write_text(json.dumps(run_rows, ensure_ascii=False, indent=2), encoding="utf-8")

    url_sets: dict[tuple[str, str, int], set[str]] = {}
    for item in url_rows:
        if item["in_scope"]:
            url_sets.setdefault((item["site_id"], item["tool"], item["round"]), set()).add(item["normalized_url"])
    aggregate_groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in run_rows:
        aggregate_groups.setdefault((row["site_id"], row["tool"], row["mode"]), []).append(row)
    aggregate_rows: list[dict[str, Any]] = []
    for (site_id, tool_name, mode), rows in sorted(aggregate_groups.items()):
        valid_rows = [row for row in rows if row["status"] in {"completed", "recovered"}]
        sets = [url_sets.get((site_id, tool_name, row["round"]), set()) for row in valid_rows]
        pairwise = [
            len(left & right) / len(left | right) if left | right else 1.0
            for i, left in enumerate(sets) for right in sets[i + 1:]
        ]
        def mean(key: str) -> float | str:
            values = [float(row[key]) for row in valid_rows if row.get(key) not in (None, "")]
            return round(sum(values) / len(values), 2) if values else ""
        aggregate_rows.append({
            "site_id": site_id, "tool": tool_name, "mode": mode,
            "rounds": len(rows), "known_exit_rounds": sum(row["status"] == "completed" for row in rows),
            "recovered_exit_unknown": sum(row["status"] == "recovered" for row in rows),
            "failed_or_interrupted": sum(row["status"] in {"failed", "timeout", "interrupted"} for row in rows),
            "mean_unique_urls": mean("unique_urls"), "mean_in_scope_urls": mean("in_scope_urls"),
            "mean_result_urls": mean("result_urls"), "mean_discovery_urls": mean("discovery_urls"),
            "mean_out_scope_urls": mean("out_scope_urls"), "mean_unique_only": mean("unique_only"),
            "round_stability_jaccard": round(sum(pairwise) / len(pairwise), 3) if pairwise else "",
            "mean_seconds": mean("duration_seconds"),
        })
    save_csv(output_dir / "aggregate.csv", aggregate_rows, [
        "site_id", "tool", "mode", "rounds", "known_exit_rounds", "recovered_exit_unknown",
        "failed_or_interrupted", "mean_unique_urls", "mean_in_scope_urls", "mean_out_scope_urls",
        "mean_result_urls", "mean_discovery_urls", "mean_unique_only", "round_stability_jaccard", "mean_seconds",
    ])

    lines = [
        "# 爬虫对比结果", "", f"运行目录：`{output_dir}`", "",
        "URL 数量来自各工具原始输出的 URL 抽取和统一归一化。该表不代表 URL 当前可访问性；差异 URL 需另行复核。", "",
        "| 站点 | 类型 | 工具 | 模式 | 轮次 | 状态 | 唯一 URL | 站内 | 站外 | 独有站内 URL | 耗时(秒) | 版本 |",
        "|---|---|---|---|---:|---|---:|---:|---:|---:|---:|---|",
    ]
    for row in run_rows:
        unique_display = row["unique_only"] if row["unique_only"] is not None else "n/a"
        lines.append(
            f"| {row['site_id']} | {row['profile']} | {row['tool']} | {row['mode']} | {row['round']} "
            f"| {row['status']} | {row['unique_urls']} | {row['in_scope_urls']} | {row['out_scope_urls']} "
            f"| {unique_display} | {row['duration_seconds']:.2f} | {row['version']} |"
        )
    lines.extend(["", "原始输出和命令记录保存在每个 `runs/<site>/<tool>/round-N/` 子目录。", ""])
    lines.extend([
        "", "## Per-tool summary", "",
        "| Site | Tool | Mode | Rounds | Mean in-scope URLs | Mean unique-only URLs | Round stability (Jaccard) | Failed/interrupted |",
        "|---|---|---|---:|---:|---:|---:|---:|",
    ])
    for row in aggregate_rows:
        stability = row["round_stability_jaccard"] if row["round_stability_jaccard"] != "" else "n/a"
        lines.append(
            f"| {row['site_id']} | {row['tool']} | {row['mode']} | {row['rounds']} "
            f"| {row['mean_in_scope_urls']} | {row['mean_unique_only']} | {stability} | {row['failed_or_interrupted']} |"
        )
    lines.extend([
        "", "## Candidate and timing counts", "",
        "Result URLs come from the tool output or standard streams. Discovery URLs come from source-edge records when the tool provides them. Unique URL totals are the deduplicated union.", "",
        "| Site | Tool | Mean result URLs | Mean discovery URLs | Mean unique in-scope URLs | Mean runtime (s) |",
        "|---|---|---:|---:|---:|---:|",
    ])
    for row in aggregate_rows:
        lines.append(
            f"| {row['site_id']} | {row['tool']} | {row.get('mean_result_urls', '')} "
            f"| {row.get('mean_discovery_urls', '')} | {row['mean_in_scope_urls']} | {row['mean_seconds']} |"
        )
    lines.extend(["", "Recovered runs have an unknown exit code; `known_exit_rounds` counts only runs whose exit code was recorded."])
    lines.extend(["", "Raw outputs and command records are under `runs/<site>/<tool>/round-N/`.", ""])
    (output_dir / "summary.md").write_text("\n".join(lines), encoding="utf-8")


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="统一运行 crawler 对比测试并生成逐站点结果表")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("list-targets", help="列出内置测试目标")
    sub.add_parser("list-tools", help="列出内置工具适配器")
    self_test = sub.add_parser("self-test", help="离线验证 URL 归一化、scope 和提取")
    self_test.set_defaults(action="self-test")
    recover = sub.add_parser("recover", help="从已保存的运行目录重建报告")
    recover.add_argument("--output-dir", type=Path, required=True)
    recover.add_argument("--targets", default="all", help="可选 site_id 列表；默认恢复整个运行目录")
    verify = sub.add_parser("verify", help="抽样复核站内 URL 候选的当前状态")
    verify.add_argument("--run-dir", type=Path, required=True)
    verify.add_argument("--tool", choices=sorted(TOOLS), help="抽样验证指定工具的全部站内候选 URL，包括共有 URL")
    verify.add_argument("--limit-per-host", type=int, default=20)
    verify.add_argument("--timeout", type=int, default=10)
    verify.add_argument("--pause", type=float, default=1.0)
    for action in ("plan", "run"):
        command = sub.add_parser(action, help="显示运行矩阵" if action == "plan" else "执行运行矩阵并生成 CSV/Markdown 报告")
        command.add_argument("--targets", default="testasp", help="逗号分隔 site_id，或 all")
        command.add_argument("--tools", default="basecrawler,browcrawler,katana,hakrawler,gospider,cariddi,urlfinder,crawlergo,argo,jsowl,jshunter")
        command.add_argument("--targets-file", type=Path, help="可选 JSON 目标清单，覆盖内置清单")
        command.add_argument("--rounds", type=int, default=1)
        command.add_argument("--output-dir", type=Path, help="run 输出目录；默认 artifacts/crawler-comparison/<run_id>")
        command.add_argument("--browcrawler-config", type=Path, help="browcrawler config.yaml 路径")
        command.add_argument("--chrome", type=Path, help="Chrome/Edge 可执行文件路径")
        command.add_argument("--allow-form-submit", action="store_true", help="允许运行会自动填充并提交表单的 crawlergo（仅限授权测试实例）")
        command.add_argument("--pause", type=float, default=1.0, help="同一目标下工具运行之间的等待秒数")
        if action == "run":
            command.add_argument("--process-timeout", type=int, default=600, help="每个工具进程的硬超时秒数")
    return parser


def selected(items: str, available: dict[str, Any], label: str) -> list[str]:
    values = list(available) if items.strip().lower() == "all" else [x.strip().lower() for x in items.split(",") if x.strip()]
    unknown = [value for value in values if value not in available]
    if unknown:
        raise ValueError(f"unknown {label}: {', '.join(unknown)}")
    return values


def plan_rows(
    targets: list[Target], tool_names: list[str], rounds: int,
    allow_form_submit: bool, chrome: Path | None = None,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for target in targets:
        for name in tool_names:
            tool = TOOLS[name]
            status = "ready"
            binary = resolve_tool_binary(name)
            if name in {"basecrawler", "browcrawler"}:
                if name == "basecrawler" and binary is None and not shutil.which("go"):
                    status = "missing binary or PATH entry"
                elif name == "browcrawler" and binary is None and not shutil.which("go"):
                    status = "missing binary and Go"
            elif binary is None:
                status = "missing binary"
            if name == "crawlergo" and not allow_form_submit:
                status = "skipped: auto form submit requires --allow-form-submit"
            elif name in {"crawlergo", "argo"} and not chrome:
                status = "missing Chrome/Edge; pass --chrome"
            elif name == "browcrawler" and not (ROOT / "browcrawler" / "third" / "obscura" / "obscura.exe").is_file():
                status = "missing browcrawler/third/obscura/obscura.exe"
            rows.append({
                "site_id": target.site_id, "seed": target.seed, "tool": name,
                "mode": tool.mode, "rounds": str(rounds), "status": status,
            })
    return rows


def run_one(
    name: str,
    target: Target,
    round_number: int,
    run_dir: Path,
    *,
    chrome: Path | None,
    brow_config: Path | None,
    allow_form_submit: bool,
    process_timeout: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    tool = TOOLS[name]
    run_dir.mkdir(parents=True, exist_ok=True)
    stdout_path, stderr_path = run_dir / "stdout.txt", run_dir / "stderr.txt"
    command, cwd, issue = build_command(
        name, target, run_dir, chrome=chrome, brow_config=brow_config,
        allow_form_submit=allow_form_submit,
    )
    version = get_version(name)
    status, exit_code, error, stdout, stderr = "completed", None, "", "", ""
    started = time.monotonic()
    if issue or command is None:
        status, error = "skipped", issue or "no command generated"
    else:
        input_text = target.seed + "\n" if name in {"hakrawler", "cariddi"} else None
        command_record = {
            "command": command,
            "cwd": str(cwd),
            "target": asdict(target),
            "version": version,
            "round": round_number,
        }
        (run_dir / "command.json").write_text(json.dumps(command_record, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            completed = subprocess.run(
                command, cwd=cwd, input=input_text, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=process_timeout, check=False,
                encoding="utf-8", errors="replace",
            )
            exit_code, stdout, stderr = completed.returncode, completed.stdout, completed.stderr
            if exit_code != 0:
                status = "failed"
                error = f"process exited with {exit_code}"
        except subprocess.TimeoutExpired as exc:
            status, error = "timeout", f"exceeded {process_timeout}s"
            stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else (exc.stdout or b"").decode("utf-8", "replace")
            stderr = (exc.stderr or "") if isinstance(exc.stderr, str) else (exc.stderr or b"").decode("utf-8", "replace")
        except OSError as exc:
            status, error = "failed", str(exc)
    duration = time.monotonic() - started
    stdout_path.write_text(stdout, encoding="utf-8", errors="replace")
    stderr_path.write_text(stderr, encoding="utf-8", errors="replace")
    raw_urls = collect_output_urls(run_dir, stdout, stderr)
    result_url_count, discovery_url_count = collect_result_and_discovery_counts(run_dir, stdout, stderr)
    url_rows: list[dict[str, Any]] = []
    for raw in sorted(raw_urls):
        normalized = normalize_url(raw)
        if not normalized:
            continue
        url_rows.append({
            "site_id": target.site_id, "tool": name, "mode": tool.mode,
            "round": round_number, "raw_url": raw, "normalized_url": normalized,
            "in_scope": in_scope(normalized, target),
        })
    (run_dir / "urls.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in url_rows), encoding="utf-8"
    )
    unique_inside = {row["normalized_url"] for row in url_rows if row["in_scope"]}
    unique_outside = {row["normalized_url"] for row in url_rows if not row["in_scope"]}
    run_row: dict[str, Any] = {
        "site_id": target.site_id, "profile": target.profile, "tool": name,
        "mode": tool.mode, "round": round_number, "version": version,
        "status": status, "exit_code": exit_code, "duration_seconds": duration,
        "result_urls": result_url_count, "discovery_urls": discovery_url_count,
        "unique_urls": len(unique_inside | unique_outside), "in_scope_urls": len(unique_inside),
        "out_scope_urls": len(unique_outside), "command": json.dumps(command or [], ensure_ascii=False),
        "stdout_file": str(stdout_path), "stderr_file": str(stderr_path), "error": error,
    }
    (run_dir / "run-result.json").write_text(
        json.dumps(run_row, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return run_row, url_rows


def recover_report(output_dir: Path, site_ids: set[str] | None = None) -> int:
    metadata_path = output_dir / "metadata.json"
    if not metadata_path.is_file():
        raise ValueError(f"missing run metadata: {metadata_path}")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    targets = {
        str(item["site_id"]): Target(
            site_id=str(item["site_id"]), seed=str(item["seed"]),
            profile=str(item.get("profile", "custom")),
            allowed_hosts=tuple(item.get("allowed_hosts", [])),
            depth=int(item.get("depth", 3)), max_pages=int(item.get("max_pages", 500)),
            concurrency=int(item.get("concurrency", 10)), timeout=int(item.get("timeout", 10)),
            host_rate=float(item.get("host_rate", 1)), optional=bool(item.get("optional", False)),
            max_response_bytes=int(item.get("max_response_bytes", 2_097_152)),
            proxy=str(item.get("proxy", "")),
            disable_bootstrap=bool(item.get("disable_bootstrap", False)),
        ) for item in metadata.get("targets", [])
    }
    if site_ids is not None:
        unknown = site_ids - targets.keys()
        if unknown:
            raise ValueError(f"unknown sites in recovery metadata: {', '.join(sorted(unknown))}")
    run_rows: list[dict[str, Any]] = []
    url_rows: list[dict[str, Any]] = []
    for command_path in sorted((output_dir / "runs").glob("*/*/round-*/command.json")):
        run_dir = command_path.parent
        record = json.loads(command_path.read_text(encoding="utf-8"))
        target = targets.get(str(record.get("target", {}).get("site_id", "")))
        if target is None:
            continue
        if site_ids is not None and target.site_id not in site_ids:
            continue
        tool_name = run_dir.parent.name
        if tool_name not in TOOLS:
            continue
        result_path = run_dir / "run-result.json"
        if result_path.is_file():
            row = json.loads(result_path.read_text(encoding="utf-8"))
            row["mode"] = TOOLS[tool_name].mode
            stdout_path = run_dir / "stdout.txt"
            stderr_path = run_dir / "stderr.txt"
            saved_output = "\n".join(
                path.read_text(encoding="utf-8", errors="replace")
                for path in (stdout_path, stderr_path) if path.is_file()
            )
            lower_output = saved_output.lower()
            known_failures = (
                "you need input target or targetsfile", "panic:", "access is denied",
                "unknown flag", "no such file or directory", "exit status ",
            )
            if any(marker in lower_output for marker in known_failures):
                row["status"] = "failed"
                row["error"] = "recovered tool error output"
        else:
            stdout_path, stderr_path = run_dir / "stdout.txt", run_dir / "stderr.txt"
            stdout = stdout_path.read_text(encoding="utf-8", errors="replace") if stdout_path.exists() else ""
            stderr = stderr_path.read_text(encoding="utf-8", errors="replace") if stderr_path.exists() else ""
            round_match = re.fullmatch(r"round-(\d+)", run_dir.name)
            round_number = int(round_match.group(1)) if round_match else 0
            exit_match = re.search(r"exit status (\d+)", stderr)
            lower_output = (stdout + "\n" + stderr).lower()
            known_failures = (
                "you need input target or targetsfile", "panic:", "access is denied",
                "unknown flag", "no such file or directory",
            )
            if not stdout_path.exists() or not stderr_path.exists():
                status, exit_code = "interrupted", None
                error = "process did not reach report write"
            elif exit_match:
                status, exit_code = "failed", int(exit_match.group(1))
                error = f"recovered nonzero exit status {exit_code}"
            elif any(marker in lower_output for marker in known_failures):
                status, exit_code = "failed", None
                error = "recovered tool error output"
            else:
                status, exit_code = "recovered", None
                error = "process output recovered; exit code was not recorded"
            urls_path = run_dir / "urls.jsonl"
            found_rows: list[dict[str, Any]] = []
            if urls_path.is_file():
                for line in urls_path.read_text(encoding="utf-8", errors="replace").splitlines():
                    try:
                        found_rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
            else:
                for raw_url in sorted(collect_output_urls(run_dir, stdout, stderr)):
                    normalized = normalize_url(raw_url)
                    if normalized:
                        found_rows.append({
                            "site_id": target.site_id, "tool": tool_name,
                            "mode": TOOLS[tool_name].mode, "round": round_number,
                            "raw_url": raw_url, "normalized_url": normalized,
                            "in_scope": in_scope(normalized, target),
                        })
            inside = {item["normalized_url"] for item in found_rows if item.get("in_scope")}
            outside = {item["normalized_url"] for item in found_rows if not item.get("in_scope")}
            row = {
                "site_id": target.site_id, "profile": target.profile, "tool": tool_name,
                "mode": TOOLS[tool_name].mode, "round": round_number,
                "version": record.get("version", "unknown"), "status": status,
                "exit_code": exit_code, "duration_seconds": 0.0,
                "unique_urls": len(inside | outside), "in_scope_urls": len(inside),
                "out_scope_urls": len(outside), "command": json.dumps(record.get("command", []), ensure_ascii=False),
                "stdout_file": str(stdout_path), "stderr_file": str(stderr_path), "error": error,
            }
            (run_dir / "urls.jsonl").write_text(
                "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in found_rows),
                encoding="utf-8",
            )
            (run_dir / "run-result.json").write_text(
                json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        stdout_path, stderr_path = run_dir / "stdout.txt", run_dir / "stderr.txt"
        stdout = stdout_path.read_text(encoding="utf-8", errors="replace") if stdout_path.exists() else ""
        stderr = stderr_path.read_text(encoding="utf-8", errors="replace") if stderr_path.exists() else ""
        round_number = int(row["round"])
        found_rows = []
        for raw_url in sorted(collect_output_urls(run_dir, stdout, stderr)):
            normalized = normalize_url(raw_url)
            if normalized:
                found_rows.append({
                    "site_id": target.site_id, "tool": tool_name,
                    "mode": TOOLS[tool_name].mode, "round": round_number,
                    "raw_url": raw_url, "normalized_url": normalized,
                    "in_scope": in_scope(normalized, target),
                })
        inside = {item["normalized_url"] for item in found_rows if item["in_scope"]}
        outside = {item["normalized_url"] for item in found_rows if not item["in_scope"]}
        row.update({
            "mode": TOOLS[tool_name].mode, "unique_urls": len(inside | outside),
            "in_scope_urls": len(inside), "out_scope_urls": len(outside),
        })
        (run_dir / "urls.jsonl").write_text(
            "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in found_rows),
            encoding="utf-8",
        )
        (run_dir / "run-result.json").write_text(
            json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        url_rows.extend(found_rows)
        run_rows.append(row)
    if not run_rows:
        raise ValueError(f"no run directories found under {output_dir / 'runs'}")
    write_reports(output_dir, run_rows, url_rows)
    print(f"recovered report: {output_dir / 'summary.md'}")
    return 0


def select_verification_candidates(
    grouped: dict[tuple[str, str, int], dict[str, set[str]]],
    source_tool: str | None = None,
) -> dict[tuple[str, str], dict[str, set[int]]]:
    candidates: dict[tuple[str, str], dict[str, set[int]]] = {}
    for (site_id, _mode, round_number), tools_for_site in grouped.items():
        if source_tool:
            for url in tools_for_site.get(source_tool, set()):
                candidates.setdefault((site_id, url), {}).setdefault(source_tool, set()).add(round_number)
            continue
        if len(tools_for_site) < 2:
            continue
        for tool_name, urls in tools_for_site.items():
            peers = set().union(*(other for peer, other in tools_for_site.items() if peer != tool_name))
            for url in urls - peers:
                candidates.setdefault((site_id, url), {}).setdefault(tool_name, set()).add(round_number)
    return candidates


def verify_unique_urls(
    run_dir: Path,
    *,
    limit_per_host: int,
    timeout: int,
    pause: float,
    source_tool: str | None = None,
) -> int:
    results_path, urls_path = run_dir / "results.csv", run_dir / "urls.csv"
    if not results_path.is_file() or not urls_path.is_file():
        raise ValueError("run directory must contain results.csv and urls.csv")
    target_map = load_targets(None)
    metadata_path = run_dir / "metadata.json"
    if metadata_path.is_file():
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        for item in metadata.get("targets", []):
            target = target_from_json(item)
            target_map[target.site_id] = target
    with results_path.open(encoding="utf-8-sig", newline="") as handle:
        result_rows = list(csv.DictReader(handle))
    valid_runs = {
        (row["site_id"], row["tool"], int(row["round"]))
        for row in result_rows if row["status"] in {"completed", "recovered"}
    }
    grouped: dict[tuple[str, str, int], dict[str, set[str]]] = {}
    with urls_path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            run_key = (row["site_id"], row["tool"], int(row["round"]))
            if row["in_scope"].lower() == "true" and run_key in valid_runs:
                key = (row["site_id"], row["mode"], int(row["round"]))
                grouped.setdefault(key, {}).setdefault(row["tool"], set()).add(row["normalized_url"])
    candidates = select_verification_candidates(grouped, source_tool)
    curl = shutil.which("curl.exe") or shutil.which("curl")
    if not curl:
        raise ValueError("curl.exe is required for online URL verification")

    rows: list[dict[str, Any]] = []
    checked_by_host: dict[str, int] = {}
    ordered_candidates = sorted(
        candidates.items(),
        key=lambda item: (
            (urlsplit(item[0][1]).hostname or "").lower(),
            hashlib.sha256(item[0][1].encode("utf-8")).hexdigest(),
        ),
    )
    for (site_id, url), sources in ordered_candidates:
        target = target_map.get(site_id)
        if target is None or not in_scope(url, target):
            continue
        host = (urlsplit(url).hostname or "").lower()
        base = {
            "site_id": site_id, "url": url,
            "source_tools": ",".join(sorted(sources)),
            "source_rounds": ",".join(str(n) for n in sorted(set().union(*sources.values()))),
        }
        if checked_by_host.get(host, 0) >= limit_per_host:
            rows.append({**base, "http_status": "", "classification": "limit_not_checked",
                         "final_url": "", "curl_exit": "", "error": "per-host verification cap"})
            continue
        command = [curl, "--head", "--location", "--silent", "--show-error",
                   "--max-time", str(timeout), "--output", os.devnull,
                   "--write-out", "%{http_code}\n%{url_effective}", url]
        try:
            completed = subprocess.run(
                command, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=timeout + 5, check=False, encoding="utf-8", errors="replace",
            )
            output = completed.stdout.splitlines()
            status_code = output[0].strip() if output else "000"
            final_url = output[1].strip() if len(output) > 1 else ""
            if status_code in {"405", "501"}:
                fallback = [curl, "--range", "0-0", "--location", "--silent", "--show-error",
                            "--max-time", str(timeout), "--max-filesize", "65536", "--output", os.devnull,
                            "--write-out", "%{http_code}\n%{url_effective}", url]
                completed = subprocess.run(
                    fallback, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    timeout=timeout + 5, check=False, encoding="utf-8", errors="replace",
                )
                output = completed.stdout.splitlines()
                status_code = output[0].strip() if output else status_code
                final_url = output[1].strip() if len(output) > 1 else final_url
            if completed.returncode and status_code == "000":
                classification = "network_error"
            elif status_code in {"404", "410"}:
                classification = "not_found"
            elif status_code in {"401", "403"}:
                classification = "restricted_or_blocked"
            elif status_code == "429":
                classification = "rate_limited"
            elif status_code.startswith("5"):
                classification = "server_error"
            elif status_code.startswith(("2", "3")):
                classification = "accessible"
            else:
                classification = "unknown"
            rows.append({**base, "http_status": status_code, "classification": classification,
                         "final_url": final_url, "curl_exit": completed.returncode,
                         "error": completed.stderr.strip()})
        except subprocess.TimeoutExpired:
            rows.append({**base, "http_status": "000", "classification": "timeout",
                         "final_url": "", "curl_exit": "", "error": f"exceeded {timeout}s"})
        checked_by_host[host] = checked_by_host.get(host, 0) + 1
        if pause > 0:
            time.sleep(pause)

    save_csv(run_dir / "verification.csv", rows, [
        "site_id", "url", "source_tools", "source_rounds", "http_status",
        "classification", "final_url", "curl_exit", "error",
    ])
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["classification"]] = counts.get(row["classification"], 0) + 1
    verification_mode = (
        f"Checks a deterministic SHA-256 sample of all in-scope candidates from `{source_tool}`, including URLs shared with other tools."
        if source_tool else
        "Checks a deterministic SHA-256 sample of in-scope URLs found by one tool but not its peers."
    )
    summary = [
        "# Candidate URL verification" if source_tool else "# Unique URL verification", "",
        verification_mode + " Uses HEAD with redirect following and a one-byte ranged GET fallback when HEAD is unsupported.", "",
        f"Run directory: `{run_dir}`", "", "| Classification | Count |", "|---|---:|",
    ]
    summary.extend(f"| {name} | {count} |" for name, count in sorted(counts.items()))
    (run_dir / "verification.md").write_text("\n".join(summary) + "\n", encoding="utf-8")
    checked_count = sum(row["classification"] != "limit_not_checked" for row in rows)
    candidate_label = "candidate URLs" if source_tool else "peer-unique URLs"
    print(f"checked {checked_count}/{len(candidates)} {candidate_label}")
    print(f"verification report: {run_dir / 'verification.md'}")
    return 0


def self_test() -> int:
    assert normalize_url("HTTP://Example.COM:80/a/../b?q=1#part") == "http://example.com/b?q=1"
    assert normalize_url("https://example.com:443") == "https://example.com/"
    assert normalize_url("ftp://example.com/a") is None
    target = _target("x", "https://example.com/", "test")
    assert in_scope("https://example.com/path", target)
    wildcard_target = Target("x", "https://example.com/", "test", ("*.example.com",), 1, 1, 1, 1, 1)
    assert in_scope("https://sub.example.com/path", wildcard_target)
    assert not in_scope("https://example.com.evil.test/path", target)
    custom = target_from_json({
        "site_id": "custom", "seed": "https://example.com/", "allowed_hosts": "example.com",
        "host_rate": 1.0,
    })
    assert custom.allowed_hosts == ("example.com",)
    unbounded = target_from_json({
        "site_id": "unbounded", "seed": "https://example.com/", "allowed_hosts": ["example.com"],
        "depth": 0, "max_response_bytes": 16_777_216,
        "proxy": "socks5://127.0.0.1:10808", "disable_bootstrap": True,
    })
    assert tool_depth("basecrawler", unbounded) == 0
    assert tool_depth("gospider", unbounded) == 0
    assert tool_depth("katana", unbounded) == 9_999_999
    urls = extract_urls('\x1b[32mURL: https://example.com/a?q=1#x\x1b[0m\n{"url":"http://other.test/b"}')
    assert "https://example.com/a?q=1#x" in urls
    assert "http://other.test/b" in urls
    structured = extract_urls('{"url":"https://example.com/good","body":"https://example.com/false-positive"}')
    assert structured == {"https://example.com/good"}
    assert normalize_url("https://example.com/chunk.js:12:99") == "https://example.com/chunk.js"
    sample_group = {("x", "static", 1): {
        "basecrawler": {"https://example.com/shared", "https://example.com/base-only"},
        "katana": {"https://example.com/shared", "https://example.com/peer-only"},
    }}
    all_base_candidates = select_verification_candidates(sample_group, "basecrawler")
    assert set(all_base_candidates) == {
        ("x", "https://example.com/shared"), ("x", "https://example.com/base-only"),
    }
    peer_unique_candidates = select_verification_candidates(sample_group)
    assert set(peer_unique_candidates) == {
        ("x", "https://example.com/base-only"), ("x", "https://example.com/peer-only"),
    }
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        assert browcrawler_static_budget(custom) == (custom.max_pages if custom.max_pages > 0 else 200)
        command, _, error = build_command("basecrawler", custom, temp_path)
        assert error is None and command is not None
        rate_index = command.index("--rl")
        assert command[rate_index + 1] == "1"
        report_index = command.index("--report")
        assert command[report_index + 1] == str(temp_path / "scan-report.json")
        discovery_index = command.index("--discoveries")
        assert command[discovery_index + 1] == str(temp_path / "discoveries.jsonl")
        jshunter_command, _, error = build_command("jshunter", unbounded, temp_path)
        assert error is None and jshunter_command is not None
        assert jshunter_command[jshunter_command.index("--crawl") + 1] == "9999999"
        assert jshunter_command[jshunter_command.index("--max-bytes") + 1] == "16777216"
        assert jshunter_command[jshunter_command.index("--proxy") + 1] == "127.0.0.1:10808"
        argo_command, _, error = build_command("argo", unbounded, temp_path, chrome=Path("chrome.exe"))
        assert error is None and argo_command is not None
        assert argo_command[argo_command.index("--hostrate") + 1] == "1"
        assert argo_command[argo_command.index("--proxy") + 1] == "socks5://127.0.0.1:10808"
        base_command, _, error = build_command("basecrawler", unbounded, temp_path)
        assert error is None and base_command is not None
        assert "--no-probe-openapi" in base_command and "--no-sitemap" in base_command
        assert base_command[base_command.index("--max-body") + 1] == "16777216"
        brow_command, _, error = build_command("browcrawler", unbounded, temp_path)
        assert error is None and brow_command is not None
        assert "--obey-robots" not in brow_command
        brow_config_text = (temp_path / "browcrawler-config.yaml").read_text(encoding="utf-8")
        assert "max_body_size: 16777216" in brow_config_text
        assert 'upstream_proxy: "socks5://127.0.0.1:10808"' in brow_config_text
        crawlergo_command, _, error = build_command(
            "crawlergo", unbounded, temp_path, chrome=Path("chrome.exe"), allow_form_submit=True
        )
        assert error is None and crawlergo_command is not None
        assert crawlergo_command[-3:-1] == ["--request-proxy", "socks5://127.0.0.1:10808"]
        assert crawlergo_command[-1] == unbounded.seed
        (temp_path / "tool-output.jsonl").write_text(
            '{"url":"https://example.com/visited"}\n', encoding="utf-8"
        )
        (temp_path / "discoveries.jsonl").write_text(
            '{"url":"https://example.com/visited"}\n{"url":"https://example.com/candidate"}\n',
            encoding="utf-8",
        )
        assert collect_result_and_discovery_counts(temp_path, "", "") == (1, 2)
    print("self-test passed")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = make_parser()
    args = parser.parse_args(argv)
    if args.action == "self-test":
        return self_test()
    if args.action == "recover":
        try:
            site_ids = None if args.targets.strip().lower() == "all" else {
                item.strip() for item in args.targets.split(",") if item.strip()
            }
            return recover_report(args.output_dir.resolve(), site_ids)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            parser.error(str(exc))
    if args.action == "verify":
        if args.limit_per_host < 1 or args.timeout < 1 or args.pause < 0:
            parser.error("--limit-per-host and --timeout must be positive; --pause cannot be negative")
        try:
            return verify_unique_urls(
                args.run_dir.resolve(), limit_per_host=args.limit_per_host,
                timeout=args.timeout, pause=args.pause, source_tool=args.tool,
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            parser.error(str(exc))
    if args.action == "list-targets":
        for target in TARGETS.values():
            suffix = " (optional)" if target.optional else ""
            print(f"{target.site_id:20} {target.profile:20} {target.seed}{suffix}")
        return 0
    if args.action == "list-tools":
        for tool in TOOLS.values():
            print(f"{tool.name:16} {tool.mode:12} {resolve_tool_binary(tool.name) or 'missing'}")
        return 0

    try:
        target_map = load_targets(args.targets_file)
        target_ids = selected(args.targets, target_map, "targets")
        tool_names = selected(args.tools, TOOLS, "tools")
        if args.rounds < 1:
            raise ValueError("--rounds must be at least 1")
        targets = [target_map[name] for name in target_ids]
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        parser.error(str(exc))

    chrome = find_chrome(args.chrome)
    plan = plan_rows(targets, tool_names, args.rounds, args.allow_form_submit, chrome)
    if args.action == "plan":
        print("site_id\ttool\tmode\trounds\tstatus\tseed")
        for row in plan:
            print("\t".join(row[key] for key in ("site_id", "tool", "mode", "rounds", "status", "seed")))
        return 0

    run_id = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    output_dir = args.output_dir or (DEFAULT_OUTPUT_ROOT / run_id)
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    brow_config = args.browcrawler_config.resolve() if args.browcrawler_config else None
    metadata = {
        "run_id": run_id,
        "started_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "python": sys.version,
        "root": str(ROOT),
        "targets": [asdict(target) for target in targets],
        "tools": tool_names,
        "rounds": args.rounds,
        "chrome": str(chrome) if chrome else None,
        "form_submit_allowed": args.allow_form_submit,
    }
    (output_dir / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    run_rows: list[dict[str, Any]] = []
    url_rows: list[dict[str, Any]] = []
    for target in targets:
        ordered_tools = tool_names[:]
        for round_number in range(1, args.rounds + 1):
            offset = (round_number - 1) % max(1, len(ordered_tools))
            this_round = ordered_tools[offset:] + ordered_tools[:offset]
            for name in this_round:
                run_dir = output_dir / "runs" / target.site_id / name / f"round-{round_number}"
                print(f"[{target.site_id}] {name} round {round_number}/{args.rounds}", flush=True)
                row, found = run_one(
                    name, target, round_number, run_dir,
                    chrome=chrome, brow_config=brow_config,
                    allow_form_submit=args.allow_form_submit,
                    process_timeout=args.process_timeout,
                )
                run_rows.append(row)
                url_rows.extend(found)
                if args.pause > 0:
                    time.sleep(args.pause)
    write_reports(output_dir, run_rows, url_rows)
    print(f"report: {output_dir / 'summary.md'}")
    print(f"csv:    {output_dir / 'results.csv'}")
    return 0 if all(row["status"] in {"completed", "skipped"} for row in run_rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
