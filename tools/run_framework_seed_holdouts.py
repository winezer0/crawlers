"""Validate independent framework entry pages against predeclared contracts."""
import argparse
from pathlib import Path
import shutil

from crawler_retained_acceptance import identity, negative_audit, observations, save, score
from katana_enhanced_baseline import deployment_fingerprint, with_scan_requests
from run_local_site_regression import SITE_CONFIG, crawl_site, launch_service, read_json_lines, stop_service, wait_for_service

CASES = (
    {"id": "nuxt-client", "site": "local-nuxt", "seed": "/nuxt/client",
     "targets": ("/api/nuxt-click?source=client-button", "/nuxt/rendering?via=navigateTo"),
     "runtime": ("/api/nuxt-click?source=client-button",)},
    {"id": "nuxt-rendering", "site": "local-nuxt", "seed": "/nuxt/rendering",
     "targets": ("/api/nuxt-catalog?view=rendering", "/nuxt/client?via=rendering"), "runtime": ()},
    {"id": "react-guide", "site": "local-react", "seed": "/react/guide",
     "targets": ("/api/react-catalog", "/api/react-click?source=button", "/api/react-page?page=2"),
     "runtime": ("/api/react-click?source=button", "/api/react-page?page=2")},
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--base-binary", type=Path, required=True)
    args = parser.parse_args()
    out = args.out_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if (out / "contract.json").exists():
        raise RuntimeError("Use a new output directory")
    save(out / "contract.json", CASES)
    results = {}
    for index, case in enumerate(CASES):
        name = case["site"]
        deployment = deployment_fingerprint(name)
        config = dict(SITE_CONFIG[name], port=18745 + index, seed_path=case["seed"],
                      expected_paths=(case["seed"], *case["targets"]), expected_interfaces={}, runtime_only_paths=())
        service, port, log = launch_service(case["id"], config, shutil.which("node"), out)
        origin = f"http://127.0.0.1:{port}"
        try:
            wait_for_service(service, origin + case["seed"], log)
            run = crawl_site(case["id"], config, origin, args.base_binary.resolve(), out)
            directory = out / case["id"]
            rows = read_json_lines(directory / "tool-output.jsonl") + read_json_lines(directory / "discoveries.jsonl")
            rows = with_scan_requests(rows, directory / "scan-report.json")
            expected = {identity(origin + path): "holdout" for path in case["targets"]}
            measured = score(expected, rows, run["elapsed_seconds"])
            actual = observations(rows)
            missing_runtime = [identity(origin + path) for path in case["runtime"]
                               if not actual.get(identity(origin + path), {}).get("executed_request_call")]
            negative = negative_audit(name, origin, rows)
            results[case["id"]] = {"run": run, "score": measured, "missing_runtime": missing_runtime,
                                    "negative_controls": negative, "deployment_fingerprint": deployment,
                                    "passed": not measured["missing"] and not missing_runtime and not negative["requested"]}
            save(out / "summary.json", results)
            print(case["id"], measured["successful"], "/", len(expected), "missing runtime", missing_runtime, flush=True)
        finally:
            stop_service(service)
    return int(any(not result["passed"] for result in results.values()))


if __name__ == "__main__":
    raise SystemExit(main())
