#!/usr/bin/env python3
"""Explicitly freeze discovery-enhanced Katana profiles on a fixed deployment."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit

from crawler_retained_acceptance import browser, contract, observations, score, negative_audit, save
from run_local_crawler_matrix import (
    BASECRAWLER, BROWCRAWLER, KATANA, build_binary, fixture_fingerprint, run_basecrawler,
)
from run_local_site_regression import (
    ROOT, SITE_CONFIG, launch_service, read_json_lines, sha256_file,
    stop_service, wait_for_service,
)

MODES = ("standard", "headless", "hybrid")
CACHE = ROOT / "artifacts/crawler-comparison/katana-enhanced-baselines"
PROFILE = "discovery-enhanced-v1"


def cyclic_path(url: str) -> bool:
    parts = [part for part in urlsplit(url).path.split("/") if part]
    for width in range(1, 5):
        for offset in range(len(parts) - width * 16 + 1):
            pattern = parts[offset:offset + width]
            if parts[offset:offset + width * 16] == pattern * 16:
                return True
    return False


def trap_evidence(output: Path) -> list[str]:
    if not output.exists():
        return []
    with output.open("rb") as handle:
        handle.seek(max(0, output.stat().st_size - 1024 * 1024))
        lines = handle.read().decode("utf-8", errors="replace").splitlines()
    evidence = []
    for line in lines[-64:]:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        url = row.get("request", {}).get("endpoint", "")
        if row.get("response", {}).get("status_code") == 404 and cyclic_path(url):
            evidence.append(url)
    return list(dict.fromkeys(evidence))


def run_reference(command: list[str], directory: Path, env: dict) -> tuple[int, float, str | None]:
    started = time.monotonic()
    reason = None
    with (directory / "stdout.txt").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        while process.poll() is None:
            evidence = trap_evidence(directory / "output.jsonl")
            if len(evidence) >= 3:
                reason = "aborted_recursive_404_path_trap"
                save(directory / "abort.json", {"reason": reason, "evidence": evidence[:3],
                                               "policy": "Stop the run, do not filter or claim complete coverage"})
            elif time.monotonic() - started > 600:
                reason = "outer_process_timeout"
            if reason:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
                else:
                    process.kill()
                process.wait(timeout=20)
                break
            time.sleep(1)
    return process.returncode, time.monotonic() - started, reason


def discovery_flags(mode: str) -> list[str]:
    if mode not in MODES:
        raise ValueError(f"Unknown Katana mode: {mode}")
    flags = ["-d", "8", "-c", "8", "-rl", "100", "-timeout", "10",
             "-retry", "1", "-fs", "fqdn", "-jc", "-jsl", "-kf", "all",
             "-aff", "-fx", "-td", "-pc", "-kb-endpoints",
             "-ndef", "-duf", "-s", "breadth-first", "-j", "-sr", "-nc", "-duc"]
    if mode != "standard":
        # The installed v1.8.0 exits without crawling with -hl -ed; the local
        # option probe isolates this diagnostic-only incompatibility.
        flags += ["-hl" if mode == "headless" else "-hh", "-xhr",
                  "-pls", "heuristic", "-time-stable", "1"]
    return flags


def deployment_fingerprint(name: str) -> str:
    config = SITE_CONFIG[name]
    root = Path(config["directory"])
    directory = root / {"local-next": ".next", "local-nuxt": ".output"}.get(name, "dist")
    files = [p for p in directory.rglob("*") if p.is_file()
             and not {"cache", "diagnostics"}.intersection(p.relative_to(directory).parts)
             and p.name not in {"trace", "trace-build"}]
    if not files:
        raise RuntimeError(f"No built deployment: {directory}")
    digest = hashlib.sha256(fixture_fingerprint(name).encode())
    for path in sorted(files):
        digest.update(path.relative_to(directory).as_posix().encode())
        digest.update(b"\0")
        digest.update(bytes.fromhex(sha256_file(path)))
    return digest.hexdigest()


def freeze(name: str, mode: str, origin: str, deployment: str, chrome: Path) -> tuple[dict, list]:
    flags = discovery_flags(mode)
    profile = {"version": PROFILE, "flags": flags, "katana_sha256": sha256_file(KATANA),
               "chrome_sha256": sha256_file(chrome) if mode != "standard" else None,
               "deployment": deployment, "origin": origin}
    key = hashlib.sha256(json.dumps(profile, sort_keys=True).encode()).hexdigest()[:24]
    directory = CACHE / key / name / mode
    metadata = directory / "metadata.json"
    output = directory / "output.jsonl"
    if metadata.exists():
        result = json.loads(metadata.read_text(encoding="utf-8"))
        if not output.exists() or result.get("output_sha256") != sha256_file(output):
            raise RuntimeError(f"Frozen output missing or modified: {directory}")
        return {**result, "cache_reused": True}, read_json_lines(output)
    directory.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents interrupted or concurrent runs from silently rescanning.
    with (directory / "attempt.json").open("x", encoding="utf-8") as handle:
        json.dump(profile, handle, indent=2)
    command = [str(KATANA), "-u", origin + SITE_CONFIG[name]["seed_path"], *flags,
               "-o", str(output), "-srd", str(directory / "responses"),
               "-elog", str(directory / "errors.jsonl")]
    if mode != "standard":
        command += ["-sc", "-scp", str(chrome)]
    save(directory / "command.json", {"command": command, "profile": profile})
    env = os.environ.copy()
    for variable in list(env):
        if variable.lower() in {"http_proxy", "https_proxy", "all_proxy"}:
            env.pop(variable)
    env.update(NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost")
    code, elapsed, abort_reason = run_reference(command, directory, env)
    rows = read_json_lines(output)
    log = (directory / "stdout.txt").read_text(encoding="utf-8", errors="replace")
    valid = code == 0 and bool(rows) and "could not create runner" not in log
    result = {"profile": profile, "tool": "katana", "mode": mode,
              "fixture_fingerprint": fixture_fingerprint(name), "deployment_fingerprint": deployment,
              "exit_code": code, "elapsed_seconds": elapsed, "frozen": True, "cache_reused": False,
              "output": str(output), "output_sha256": sha256_file(output) if output.exists() else None,
              "output_rows": len(rows), "command": command, "valid": valid,
              "abort_reason": abort_reason,
              "error": None if valid else "Reference did not complete a valid crawl; do not interpret as zero coverage"}
    save(metadata, result)
    return result, rows


def differences(reference: list, actual: list) -> list[dict]:
    expected, found = observations(reference), observations(actual)
    result = []
    for label, evidence in sorted(expected.items()):
        if not evidence["successful"]:
            continue
        candidate = found.get(label, {})
        if candidate.get("successful"):
            continue
        path = urlsplit(label.split(" ", 1)[1]).path
        category = "api" if "/api/" in path else "asset" if "." in path.rsplit("/", 1)[-1] else "page"
        if "_rsc=" in label:
            category = "rsc-request-variant"
        result.append({"identity": label, "category": category, "reference": evidence,
                       "actual": candidate, "reason": "not-discovered" if not candidate else "not-successful",
                       "requires_review": True})
    return result


def with_scan_requests(rows: list, report: Path) -> list:
    # The normal result stream intentionally omits some fetched static assets.
    # Compare against the dispatch ledger before calling such assets missing.
    completion = json.loads(report.read_text(encoding="utf-8"))["completion"]
    return rows + [{"url": item.get("final_url") or item["requested_url"],
                    "method": item.get("method", "GET"), "status": item.get("http_status", 0),
                    "source": item.get("source"), "evidence": item.get("evidence"),
                    "error": item.get("error")} for item in completion.get("requests", [])]


def recheck_base(args, out):
    previous = json.loads(args.recheck_summary.read_text(encoding="utf-8"))
    if not set(args.sites) <= previous["sites"].keys():
        raise RuntimeError("Reference summary does not contain every requested site")
    binary = out / "basecrawler.exe"
    build_binary(shutil.which("go"), BASECRAWLER, binary, out / "build-basecrawler.log")
    summary = {"sites": {}, "reference_summary": str(args.recheck_summary.resolve()), "reference_rescanned": False}
    for name in args.sites:
        original = previous["sites"][name]
        if deployment_fingerprint(name) != original["deployment_fingerprint"]:
            raise RuntimeError(f"Deployment differs from the frozen reference: {name}")
        service, port, log = launch_service(name, SITE_CONFIG[name], shutil.which("node"), out)
        origin = f"http://127.0.0.1:{port}"
        try:
            wait_for_service(service, origin + SITE_CONFIG[name]["seed_path"], log)
            meta, rows = run_basecrawler(name, origin, binary, out, 1, True, 3)
            rows = with_scan_requests(rows, out / name / "basecrawler-round-1/scan-report.json")
            tools = {key: value for key, value in original["tools"].items() if key != "basecrawler"}
            tools["basecrawler"] = {"run": meta, "score": score(contract(name, origin), rows, meta["elapsed_seconds"]),
                                     "negative_controls": negative_audit(name, origin, rows)}
            gaps = {}
            for mode in MODES:
                reference = tools["katana-" + mode]
                path = Path(reference["run"]["output"])
                if sha256_file(path) != reference["run"]["output_sha256"]:
                    raise RuntimeError(f"Frozen reference modified: {path}")
                frozen = read_json_lines(path)
                reference["observed_score"] = score(contract(name, origin), frozen, reference["run"]["elapsed_seconds"])
                gaps[mode] = differences(frozen, rows)
            site = {"deployment_fingerprint": original["deployment_fingerprint"], "tools": tools, "base_differences": gaps}
            summary["sites"][name] = site
            save(out / name / "comparison.json", site)
            save(out / "summary.json", summary)
            print(name, tools["basecrawler"]["score"]["successful"], "seconds", round(meta["elapsed_seconds"], 3),
                  "differences", {mode: len(items) for mode, items in gaps.items()}, flush=True)
        finally:
            stop_service(service)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sites", nargs="+", choices=SITE_CONFIG, default=["local-nuxt", "local-react", "local-next"])
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--chrome", type=Path, default=Path("C:/Program Files/Google/Chrome/Application/chrome.exe"))
    parser.add_argument("--recheck-summary", type=Path, help="rerun only Basecrawler against an existing fixed-deployment summary")
    args = parser.parse_args()
    out = args.out_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if (out / "summary.json").exists():
        raise RuntimeError("Use a new output directory; frozen reference cache will still be reused")
    if args.recheck_summary:
        return recheck_base(args, out)
    binaries = {"basecrawler": out / "basecrawler.exe", "browcrawler": out / "browcrawler.exe"}
    for tool, module in [("basecrawler", BASECRAWLER), ("browcrawler", BROWCRAWLER)]:
        build_binary(shutil.which("go"), module, binaries[tool], out / f"build-{tool}.log")
    summary = {"profile": PROFILE, "sites": {}, "scope": "fixed local deployments; no historical public hash equivalence"}
    for name in args.sites:
        config = SITE_CONFIG[name]
        before = deployment_fingerprint(name)
        service, port, log = launch_service(name, config, shutil.which("node"), out)
        origin = f"http://127.0.0.1:{port}"
        try:
            wait_for_service(service, origin + config["seed_path"], log)
            expected = contract(name, origin)
            base_meta, base = run_basecrawler(name, origin, binaries["basecrawler"], out, 1, True, 3)
            base = with_scan_requests(base, out / name / "basecrawler-round-1/scan-report.json")
            brow_meta, brow = browser(binaries["browcrawler"], origin + config["seed_path"], out / name / "browcrawler")
            site = {"deployment_fingerprint": before, "tools": {}, "differences": {}}
            for tool, meta, rows in [("basecrawler", base_meta, base), ("browcrawler", brow_meta, brow)]:
                site["tools"][tool] = {"run": meta, "score": score(expected, rows, meta["elapsed_seconds"]),
                                       "negative_controls": negative_audit(name, origin, rows)}
            for mode in MODES:
                meta, rows = freeze(name, mode, origin, before, args.chrome)
                site["tools"]["katana-" + mode] = {"run": meta, "score": score(expected, rows, meta["elapsed_seconds"]) if meta.get("valid") else None,
                                                 "observed_score": score(expected, rows, meta["elapsed_seconds"]),
                                                 "negative_controls": negative_audit(name, origin, rows)}
                site["differences"][mode] = {"basecrawler": differences(rows, base), "browcrawler": differences(rows, brow)}
                measured = site["tools"]["katana-" + mode]["score"]
                print(name, mode, measured["successful"] if measured else "INVALID", "/", len(expected), flush=True)
            if deployment_fingerprint(name) != before:
                raise RuntimeError(f"Deployment changed during comparison: {name}")
            summary["sites"][name] = site
            save(out / name / "comparison.json", site)
            save(out / "summary.json", summary)
        finally:
            stop_service(service)
    return int(any(tool["run"]["exit_code"] != 0 or tool["run"].get("valid") is False for site in summary["sites"].values()
                   for tool in site["tools"].values()))


if __name__ == "__main__":
    raise SystemExit(main())
