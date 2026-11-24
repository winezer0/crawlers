"""Verify retained discovery capabilities with frozen references and Obscura.

References are read only. Full method/URL identities are preserved; route-related
RSC requests are reported separately and never treated as exact coverage.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import socket
from urllib.parse import parse_qsl, urljoin, urlsplit, urlunsplit

from crawler_runtime_replay import Frontend
from run_local_crawler_matrix import (
    BROWCRAWLER, KATANA_BASELINES, build_binary, fixture_fingerprint, run_basecrawler,
)
from run_local_site_regression import (
    ROOT, BASECRAWLER, SITE_CONFIG, build_site, launch_service, npm_cli_for,
    read_json_lines, read_decoy_fixture, FALSE_CANDIDATE_FRAGMENTS,
    run_logged, sha256_file, stop_service, wait_for_service,
)

FROZEN = ROOT / "artifacts/crawler-comparison/next-rsc-evidence-20261123-v1/round-01/framework-nextjs"
DATASET = ROOT / "artifacts/crawler-comparison/next-docs-runtime-dataset-20261123"
REPLAY = ROOT / "artifacts/crawler-comparison/next-docs-layout-removal-20261124-final/summary.json"


def save(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def identity(url: str, method: str = "GET") -> str:
    part = urlsplit(url)
    # Query order, percent escapes, path case and trailing slash are significant.
    return method.upper() + " " + urlunsplit((part.scheme.lower(), part.netloc.lower(), part.path or "/", part.query, ""))


def route(url: str) -> tuple:
    part = urlsplit(url)
    return part.scheme.lower(), part.netloc.lower(), part.path or "/", tuple(
        (k, v) for k, v in parse_qsl(part.query, keep_blank_values=True) if k != "_rsc")


def observations(rows: list[dict]) -> dict[str, dict]:
    result = {}
    for row in rows:
        request, response = row.get("request") or {}, row.get("response") or {}
        url = request.get("endpoint") or request.get("url") or row.get("url")
        if not isinstance(url, str) or urlsplit(url).scheme not in ("http", "https"):
            continue
        label = identity(url, request.get("method") or row.get("method") or "GET")
        status = int(response.get("status_code") or row.get("status") or 0)
        current = result.setdefault(label, {"discovered": True, "attempted": False,
                                           "successful": False, "statuses": [], "sources": [],
                                           "executed_request_call": False})
        current["executed_request_call"] |= row.get("evidence") == "executed-request-call"
        current["attempted"] |= status > 0 or bool(row.get("error"))
        current["successful"] |= 200 <= status < 400
        if status and status not in current["statuses"]:
            current["statuses"].append(status)
        source = request.get("source") or row.get("source")
        if source and source not in current["sources"]:
            current["sources"].append(source)
    return result


def contract(name: str, origin: str) -> dict[str, str]:
    config = SITE_CONFIG[name]
    expected = {identity(origin + path): "page_or_catalog" for path in config["expected_paths"]}
    expected.update({identity(origin + path, method): "interface"
                     for path, method in config.get("expected_interfaces", {}).items()})
    for path in config.get("runtime_only_paths", ()):
        # Authored handlers use these parameters; the legacy path-only contract
        # discarded them. These are fixture facts, never scanner special cases.
        suffix = ""
        if name in ("local-nuxt", "local-react", "local-next"):
            suffix = "?source=button" if path.endswith("-click") else "?page=2" if path.endswith("-page") else ""
        expected[identity(origin + path + suffix)] = "runtime"
    return expected


def score(expected: dict[str, str], rows: list[dict], elapsed: float) -> dict:
    actual = observations(rows)
    targets = [{"identity": label, "category": category,
                **actual.get(label, {"discovered": False, "attempted": False,
                                     "successful": False, "statuses": [], "sources": []})}
               for label, category in sorted(expected.items())]
    # Extra URLs are not automatically false positives: assets are legitimate.
    failures = [label for label, row in actual.items() if any(s >= 400 for s in row["statuses"])]
    return {"elapsed_seconds": elapsed, "expected": len(targets), "targets": targets,
            "found": sum(t["discovered"] for t in targets),
            "attempted": sum(t["attempted"] for t in targets),
            "successful": sum(t["successful"] for t in targets),
            "missing": [t["identity"] for t in targets if not t["successful"]],
            "http_failure_urls": failures,
            "unclassified_api_requests": [label for label, row in actual.items()
                if label not in expected and "/api/" in urlsplit(label.split(" ", 1)[1]).path and row["attempted"]],
            "unique_discoveries": len(actual)}


def negative_audit(name: str, origin: str, rows: list[dict]) -> dict:
    config = SITE_CONFIG[name]
    values = list(FALSE_CANDIDATE_FRAGMENTS)
    if config.get("decoy_fixture"):
        values += read_decoy_fixture(config["decoy_fixture"])
    labels = {identity(urljoin(origin + "/", value)) for value in values}
    labels -= set(contract(name, origin))
    actual = observations(rows)
    return {"control_values": len(values), "discovered": sorted(labels & actual.keys()),
            "requested": sorted(label for label in labels & actual.keys() if actual[label]["attempted"]),
            "policy": "Only authored non-request controls are confirmed false positives; other extra APIs remain unclassified."}


def frozen_local(name: str, mode: str, fingerprint: str, expected: dict) -> tuple[dict, list]:
    candidates = []
    for path in KATANA_BASELINES.glob(f"*/{name}/{mode}/metadata.json"):
        metadata = json.loads(path.read_text(encoding="utf-8"))
        output = path.parent / "output.jsonl"
        if metadata.get("exit_code") != 0 or not output.is_file():
            continue
        rows = read_json_lines(output)
        measured = score(expected, rows, float(metadata.get("elapsed_seconds") or 0))
        candidates.append((metadata.get("fixture_fingerprint") == fingerprint,
                           measured["successful"], len(observations(rows)), str(path), metadata, rows))
    if not candidates:
        return {"available": False, "reference_rescanned": False}, []
    chosen = max(candidates, key=lambda item: item[:4])
    _, _, _, path, metadata, rows = chosen
    return {"available": True, "path": path, "sha256": sha256_file(Path(path).parent / "output.jsonl"),
            "fixture_matches": chosen[0], "reference_rescanned": False, "metadata": metadata}, rows


def browser(binary: Path, seed: str, directory: Path, *, public: bool = False) -> tuple[dict, list]:
    directory.mkdir(parents=True, exist_ok=True)
    config = (BROWCRAWLER / "config.example.yaml").read_text(encoding="utf-8")
    # Keep all run files isolated; use the configured bundled browser explicitly.
    config = config.replace('obscura_bin: "./third/obscura/obscura.exe"',
                            'obscura_bin: ' + json.dumps(str(BROWCRAWLER / "third/obscura/obscura.exe")))
    config = config.replace("task_ms: 600000", "task_ms: 180000")
    config_path = directory / "config.yaml"
    config_path.write_text(config, encoding="utf-8")
    command = [str(binary), "--config", str(config_path), "--url", seed,
               "--no-basecrawler", "--scope-mode", "fqdn", "--depth", "8",
               "--max-pages", "1" if public else "100", "--interactions", "10",
               "--settle-ms", "2000" if public else "600", "--retries", "0",
               "--backend", "obscura", "--upstream", "none", "--concurrency", "1",
               "--out", str(directory / "traffic.jsonl"), "--discovered", str(directory / "discoveries.jsonl"),
               "--report", str(directory / "report.json")]
    save(directory / "command.json", {"command": command, "browser": str(BROWCRAWLER / "third/obscura/obscura.exe"),
                                      "basecrawler_bootstrap": False, "public_single_page": public})
    code, elapsed = run_logged(command, BROWCRAWLER, directory / "stdout.txt", timeout=240 if public else 600)
    traffic = read_json_lines(directory / "traffic.jsonl")
    rows = traffic + read_json_lines(directory / "discoveries.jsonl")
    result = {"exit_code": code, "elapsed_seconds": elapsed, "traffic_rows": len(traffic),
              "report": str(directory / "report.json"), "bootstrap_disabled": True}
    save(directory / "run.json", result)
    return result, rows


def audit_sources(out: Path, browser_rows: list[dict] | None = None) -> dict:
    effective = json.loads((FROZEN / "effective.json").read_text(encoding="utf-8"))
    replay = json.loads(REPLAY.read_text(encoding="utf-8"))
    html = (DATASET / "frontend.html").read_text(encoding="utf-8")
    parsed = Frontend("https://nextjs.org/docs")
    parsed.feed(html)
    manifest = json.loads((DATASET / "dataset.json").read_text(encoding="utf-8"))
    sources = []
    for url, record in manifest["modules"].items():
        path = DATASET / record["file"]
        body = path.read_bytes()
        if hashlib.sha256(body).hexdigest() != record["sha256"]:
            raise ValueError("Frozen source hash mismatch: " + str(path))
        sources.append((url, body.decode("utf-8", errors="replace")))
    browser_calls = observations(browser_rows or [])
    current_documents = [r for r in browser_rows or [] if r.get("url") == "https://nextjs.org/docs"
                         and r.get("method") == "GET" and r.get("body")]
    if current_documents:
        (out / "current-browser-document.html").write_text(current_documents[0]["body"], encoding="utf-8")
    reference_rows = read_json_lines(FROZEN / "katana-browser/results.jsonl")
    rows = []
    for url in effective["missing"]:
        plain = urlunsplit((*urlsplit(url)[:3], "", ""))
        related = [r for r in replay["captured_requests"] if r.get("Kind") != "navigation"
                   and route(r["Raw"]) == route(url)]
        proof = [r for r in reference_rows if (r.get("request") or {}).get("endpoint") == url]
        browser_related = [{"identity": label, **value} for label, value in browser_calls.items()
                           if route(label.split(" ", 1)[1]) == route(url) and value["attempted"]]
        rows.append({"reference_url": url, "page_url": plain,
                     "html_link_targets": sorted(link for link in parsed.links if route(link) == route(url)),
                     "exact_url_literal_in_html": url in html,
                     "exact_url_literal_js_sources": [source for source, body in sources if url in body],
                     "rsc_token_literal_js_sources": [source for source, body in sources
                         if dict(parse_qsl(urlsplit(url).query)).get("_rsc", "\x00") in body],
                     "page_path_literal_js_sources": [source for source, body in sources
                         if json.dumps(urlsplit(url).path) in body],
                     "frozen_response_body_available": any((r.get("response") or {}).get("body") is not None
                         or (r.get("response") or {}).get("body_file") for r in proof),
                     "base_replay_related_calls": related,
                     "browser_exact": browser_calls.get(identity(url)), "browser_related_requests": browser_related,
                     "classification": "page_link_available_runtime_variant_required",
                     "static_parser_fix_proven": False, "exact_frozen_response_coverage": False})
    result = {"rows": rows, "targets": len(rows), "source_modules_checked": len(sources),
              "exact_literals": sum(r["exact_url_literal_in_html"] or bool(r["exact_url_literal_js_sources"]) for r in rows),
              "page_links": sum(bool(r["html_link_targets"]) for r in rows),
              "policy": "Literal presence is source evidence only. Dynamic _rsc values are not manufactured. Current browser route matches do not close different deployment/hash targets.",
              "inputs": {str(p): sha256_file(p) for p in [FROZEN / "effective.json", REPLAY, DATASET / "frontend.html", DATASET / "dataset.json"]}}
    save(out / "frozen-source-audit.json", result)
    return result


def write_report(out: Path, results: list[dict], public_run: dict | None, public_rows: list[dict], scenario_paths=()) -> None:
    audit = audit_sources(out, public_rows)
    rsc_rows = [r for r in public_rows if "_rsc" in dict(parse_qsl(urlsplit(r.get("url", "")).query))]
    scenarios = [{"path": str(path.resolve()), "sha256": sha256_file(path),
                  "results": json.loads(path.read_text(encoding="utf-8"))["results"]} for path in scenario_paths]
    summary = {"sites": results, "public_browser": public_run, "source_audit_targets": audit["targets"],
               "source_audit_exact_literals": audit["exact_literals"], "reference_rescanned": False,
               "public_rsc_http_rows": len(rsc_rows), "public_rsc_protocol_successes": sum(
                   200 <= int(r.get("status") or 0) < 300 and "text/x-component" in r.get("content_type", "")
                   for r in rsc_rows),
               "public_verification_passed": bool(rsc_rows) and any(
                   200 <= int(r.get("status") or 0) < 300 and "text/x-component" in r.get("content_type", "")
                   for r in rsc_rows),
               "local_union_targets_complete": all(s["measurements"]["union"]["successful"] == s["measurements"]["union"]["expected"] for s in results),
               "basecrawler_targets_complete": all(s["measurements"]["basecrawler"]["successful"] == s["measurements"]["basecrawler"]["expected"] for s in results),
               "overall_accepted": all(s["measurements"]["basecrawler"]["successful"] == s["measurements"]["basecrawler"]["expected"] for s in results)
                   and all(r["exact_frozen_response_coverage"] for r in audit["rows"]),
               "acceptance_policy": "Full acceptance requires Basecrawler to cover every local target and frozen request context; local union success alone is insufficient.",
               "scenario_regressions": scenarios,
               "script_sha256": sha256_file(Path(__file__))}
    save(out / "summary.json", summary)
    lines = ["# 保留能力与浏览器补充验收", "", "所有交互目标保留在必测集合；完整方法与 URL 不折叠。", "",
             "| 站点 | 工具 | 发现 | 尝试 | 成功响应 | 耗时（秒） |", "|---|---|---:|---:|---:|---:|"]
    for site in results:
        for tool, row in site["measurements"].items():
            lines.append(f"| {site['site']} | {tool} | {row['found']}/{row['expected']} | {row['attempted']}/{row['expected']} | {row['successful']}/{row['expected']} | {row['elapsed_seconds']:.3f} |")
    lines.extend(["", "Katana 仅复用冻结记录。fixture_matches=false 的结果仅为历史对照，不能作为同版本验收。",
                  "成功响应只代表 HTTP 2xx/3xx，不自动代表业务语义正确。合并耗时为两个独立扫描耗时之和。",
                  "逐项目标、未覆盖项、额外 API、HTTP 失败与基线指纹见各站 comparison.json。",
                  "公网单页验证与冻结源审计见 frozen-source-audit.json；不同部署 RSC 不认定等价。",
                  f"公网 RSC 请求记录 {len(rsc_rows)} 条；本轮浏览器补充验证{'通过' if summary['public_verification_passed'] else '未通过'}。"])
    if scenarios:
        cases = [row for suite in scenarios for row in suite["results"]]
        lines.append(f"独立场景回归：{sum(bool(row['passed']) for row in cases)}/{len(cases)} 通过；逐项耗时与来源见 summary.json 的 scenario_regressions。")
    (out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def rescore(previous: Path, out: Path, scenario_paths=()) -> None:
    source = json.loads((previous / "summary.json").read_text(encoding="utf-8"))
    results = []
    for site in source["sites"]:
        name = site["site"]
        origin = "http://127.0.0.1:" + str(SITE_CONFIG[name]["port"])
        expected = contract(name, origin)
        inputs = {"basecrawler": [previous / name / "basecrawler-round-1/output.jsonl", previous / name / "basecrawler-round-1/discoveries.jsonl"],
                  "browcrawler": [previous / name / "browser/traffic.jsonl", previous / name / "browser/discoveries.jsonl"]}
        tool_rows = {tool: [row for path in paths for row in read_json_lines(path)] for tool, paths in inputs.items()}
        measurements = {}
        for tool, rows in tool_rows.items():
            old = site["measurements"][tool]
            measurements[tool] = score(expected, rows, old["elapsed_seconds"])
            for key in ("testfill", "exit_code", "bootstrap_disabled"):
                if key in old:
                    measurements[tool][key] = old[key]
            measurements[tool]["negative_controls"] = negative_audit(name, origin, rows)
            report = previous / name / ("basecrawler-round-1/scan-report.json" if tool == "basecrawler" else "browser/report.json")
            measurements[tool]["completion"] = json.loads(report.read_text(encoding="utf-8")).get("completion")
        measurements["union"] = score(expected, tool_rows["basecrawler"] + tool_rows["browcrawler"],
                                       sum(measurements[t]["elapsed_seconds"] for t in tool_rows))
        references = {}
        for mode in ("standard", "headless"):
            metadata, rows = frozen_local(name, mode, site["fixture_fingerprint"], expected)
            references[mode] = metadata
            measurements["katana-" + mode] = score(expected, rows, float(metadata.get("metadata", {}).get("elapsed_seconds") or 0))
        result = {**site, "references": references, "measurements": measurements,
                  "input_hashes": {str(path): sha256_file(path) for paths in inputs.values() for path in paths}}
        save(out / name / "contract.json", expected)
        save(out / name / "comparison.json", result)
        results.append(result)
    public_rows = read_json_lines(previous / "public-next-docs/traffic.jsonl")
    save(out / "rescore-input.json", {"path": str(previous), "summary_sha256": sha256_file(previous / "summary.json"),
                                     "network_requests": 0, "reason": "Authored interaction query parameters and JSONL Unicode separators corrected"})
    public_run = source["public_browser"]
    if public_run:
        public_run = {**public_run, "legacy_traffic_rows": public_run["traffic_rows"], "traffic_rows": len(public_rows)}
    write_report(out, results, public_run, public_rows, scenario_paths)


def require_free_port(port: int) -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", port))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--sites", default="local-nuxt,local-react,local-next")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--with-public", action="store_true", help="also verify one live Next Docs page with Obscura")
    parser.add_argument("--rescore-from", type=Path, help="rescore saved records offline without changing them")
    parser.add_argument("--scenario-summary", type=Path, action="append", default=[], help="include an existing independent scenario result")
    args = parser.parse_args()
    out = args.out_dir.resolve()
    if out.exists():
        parser.error("Use a new output directory")
    if args.rescore_from:
        out.mkdir(parents=True)
        rescore(args.rescore_from.resolve(), out, args.scenario_summary)
        print(out)
        return 0
    names = args.sites.split(",")
    if any(name not in SITE_CONFIG for name in names):
        parser.error("Unknown local fixture")
    node, go = shutil.which("node"), shutil.which("go")
    if not node or not go:
        parser.error("Node and Go are required")
    out.mkdir(parents=True)
    save(out / "contract.json", {"sites": names, "reference_rescan": False,
                                "runtime_targets_required": True, "testfill_cases_per_interface": 3,
                                "with_public": args.with_public})
    base, brow = out / "bin/basecrawler.exe", out / "bin/browcrawler.exe"
    builds = {"basecrawler": build_binary(go, BASECRAWLER, base, out / "base-build.log"),
              "browcrawler": build_binary(go, BROWCRAWLER, brow, out / "brow-build.log")}
    save(out / "builds.json", {"seconds": builds, "base_sha256": sha256_file(base), "brow_sha256": sha256_file(brow)})
    results = []
    for name in names:
        config = SITE_CONFIG[name]
        require_free_port(int(config["port"]))
        if not args.skip_build:
            build_site(name, config, node, npm_cli_for(node), out, True)
        origin = f"http://127.0.0.1:{config['port']}"
        expected = contract(name, origin)
        save(out / name / "contract.json", expected)
        fingerprint = fixture_fingerprint(name)
        service, port, log = launch_service(name, config, node, out)
        try:
            wait_for_service(service, origin + config["seed_path"], log)
            base_run, base_rows = run_basecrawler(name, origin, base, out, 1, True, 3)
            browser_run, browser_rows = browser(brow, origin + config["seed_path"], out / name / "browser")
        finally:
            stop_service(service)
        measurements = {"basecrawler": score(expected, base_rows, base_run["elapsed_seconds"]),
                        "browcrawler": score(expected, browser_rows, browser_run["elapsed_seconds"]),
                        "union": score(expected, base_rows + browser_rows,
                                       base_run["elapsed_seconds"] + browser_run["elapsed_seconds"])}
        measurements["basecrawler"]["testfill"] = base_run["expected_testfill_interfaces"]
        measurements["basecrawler"]["negative_controls"] = negative_audit(name, origin, base_rows)
        measurements["basecrawler"]["exit_code"] = base_run["exit_code"]
        measurements["browcrawler"].update(browser_run)
        measurements["browcrawler"]["negative_controls"] = negative_audit(name, origin, browser_rows)
        references = {}
        for mode in ("standard", "headless"):
            metadata, rows = frozen_local(name, mode, fingerprint, expected)
            references[mode] = metadata
            measurements["katana-" + mode] = score(expected, rows, float(metadata.get("metadata", {}).get("elapsed_seconds") or 0))
        result = {"site": name, "fixture_fingerprint": fingerprint,
                  "references": references, "measurements": measurements}
        save(out / name / "comparison.json", result)
        results.append(result)
        print(json.dumps({"site": name, "coverage": {k: f"{v['successful']}/{v['expected']}" for k, v in measurements.items()}}), flush=True)
    public_run, public_rows = None, []
    if args.with_public:
        public_run, public_rows = browser(brow, "https://nextjs.org/docs", out / "public-next-docs", public=True)
    write_report(out, results, public_run, public_rows, args.scenario_summary)
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
