"""Compare runtime request proofs, including headers and bodies, on two holdouts."""
import argparse
import hashlib
import json
import shutil
from pathlib import Path
import threading

from crawler_retained_acceptance import browser, identity, observations, save, score
from katana_enhanced_baseline import MODES, freeze
from run_local_crawler_matrix import run_basecrawler
from run_local_site_regression import SITE_CONFIG, sha256_file
from run_local_site_regression import read_json_lines
from runtime_proof_site import ACTIONS, FIXTURE, Site, VARIANTS, token


def browser_collection(rows, expected, variant, origin):
    proofs = []
    for row in rows:
        label = identity(row.get("url", ""), row.get("method", "GET"))
        if label not in expected or row.get("status") != 200:
            continue
        headers = {key.lower(): value for key, value in row.get("request_headers", {}).items()}
        try:
            body = json.loads(row.get("request_body", ""))
        except ValueError:
            body = {}
        proofs.append({"identity": label, "token_header_preserved": headers.get("x-runtime-token") == [token(variant)],
                       "request_body_preserved": body.get("variant") == variant and body.get("proof") == token(variant),
                       "response_body_preserved": bool(row.get("body")),
                       "page_association": row.get("task_id") == origin + "/" + variant,
                       "association_basis": "single controlled HTML document and matching payload variant; task_id is not a general initiator URL"})
    return {"proofs": proofs, "verified": len({p["identity"] for p in proofs}) == len(expected)
            and all(p["token_header_preserved"] and p["request_body_preserved"]
                    and p["response_body_preserved"] and p["page_association"] for p in proofs)}


def evaluate(expected, rows, events, elapsed):
    result = score(expected, rows, elapsed)
    actual = observations(rows)
    proven = {identity("http://127.0.0.1:18891" + event["path"], event["method"])
              for event in events if event["method"] == "POST" and event["status"] == 200}
    runtime = {label for label, item in actual.items() if item["executed_request_call"]}
    result.update(server_confirmed=sorted(set(expected) & proven),
                  wire_requests=len(events),
                  runtime_calls=sorted(set(expected) & runtime),
                  confirmed_without_runtime_call=sorted((set(expected) & proven) - runtime),
                  invalid_posts=[event["path"] for event in events
                                 if event["method"] == "POST" and event["status"] != 200],
                  invalid_requests=[{"method": event["method"], "path": event["path"], "status": event["status"]}
                                    for event in events if event["status"] >= 400],
                  decoy_requests=[event["path"] for event in events if "not-a-request" in event["path"]])
    result["runtime_provenance_note"] = "runtime_calls is Basecrawler instrumentation only; browser execution is checked by server-accepted token/header/body contracts"
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--base-binary", type=Path, required=True)
    parser.add_argument("--brow-binary", type=Path, required=True)
    parser.add_argument("--with-katana", action="store_true", help="explicitly freeze missing references once")
    parser.add_argument("--reuse-browser-dir", type=Path, help="reuse matching recorded Browcrawler evidence; scan only Basecrawler")
    args = parser.parse_args()
    out = args.out_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if (out / "contract.json").exists():
        raise RuntimeError("Use a new output directory")
    origin = "http://127.0.0.1:18891"
    contracts = {variant: {identity(f"{origin}/{collection}/{token(variant)}/{action}", "POST"): action
                           for action in ACTIONS} for variant, collection in VARIANTS.items()}
    if args.reuse_browser_dir:
        previous = json.loads((args.reuse_browser_dir / "summary.json").read_text(encoding="utf-8"))
        previous_contract = json.loads((args.reuse_browser_dir / "contract.json").read_text(encoding="utf-8"))
        if previous["binaries"]["brow"] != sha256_file(args.brow_binary) or previous_contract["variants"] != contracts:
            raise RuntimeError("Recorded browser binary or contract differs")
    save(out / "contract.json", {"variants": contracts, "requires": "exact token header and JSON body",
                                 "scope": "generic JS runtime fixture, not framework hydration proof"})
    server = Site()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    summary = {"variants": {}, "binaries": {"base": sha256_file(args.base_binary), "brow": sha256_file(args.brow_binary)}}
    try:
        for variant, expected in contracts.items():
            name = "runtime-" + variant
            SITE_CONFIG[name] = {"directory": FIXTURE, "seed_path": "/" + variant,
                                 "expected_paths": ("/" + variant,), "expected_interfaces": {},
                                 "runtime_only_paths": (), "source_map_prefixes": (), "port": 18891}
            deployment = hashlib.sha256((sha256_file(FIXTURE / "app.js") +
                                         sha256_file(Path(__file__).with_name("runtime_proof_site.py")) +
                                         json.dumps(expected, sort_keys=True)).encode()).hexdigest()
            measurements = {}
            for tool in ("basecrawler", "browcrawler", *("katana-" + mode for mode in MODES if args.with_katana)):
                recorded_events = None
                with server.lock:
                    server.events.clear()
                if tool == "basecrawler":
                    meta, rows = run_basecrawler(name, origin, args.base_binary.resolve(), out, 1, True, 3)
                elif tool == "browcrawler":
                    if args.reuse_browser_dir:
                        source = args.reuse_browser_dir / name / tool
                        target = out / name / tool
                        target.mkdir(parents=True, exist_ok=True)
                        for filename in ("traffic.jsonl", "discoveries.jsonl", "run.json", "command.json"):
                            shutil.copyfile(source / filename, target / filename)
                        meta = {**json.loads((source / "run.json").read_text(encoding="utf-8")),
                                "cache_reused": True, "source": str(source.resolve()),
                                "traffic_sha256": sha256_file(source / "traffic.jsonl")}
                        rows = read_json_lines(source / "traffic.jsonl") + read_json_lines(source / "discoveries.jsonl")
                        recorded_events = json.loads((source / "oracle.json").read_text(encoding="utf-8"))
                    else:
                        meta, rows = browser(args.brow_binary.resolve(), origin + "/" + variant, out / name / tool)
                else:
                    meta, rows = freeze(name, tool.removeprefix("katana-"), origin, deployment,
                                        Path("C:/Program Files/Google/Chrome/Application/chrome.exe"))
                with server.lock:
                    events = list(server.events)
                if recorded_events is not None:
                    events = recorded_events
                # Cached references have no current server events: preserve prior oracle instead.
                oracle_path = Path(meta["output"]).parent / "oracle.json" if tool.startswith("katana-") else None
                if oracle_path and meta.get("cache_reused"):
                    events = json.loads(oracle_path.read_text(encoding="utf-8")) if oracle_path.exists() else []
                elif oracle_path:
                    save(oracle_path, events)
                save(out / name / tool / "oracle.json", events)
                measured = evaluate(expected, rows, events, meta["elapsed_seconds"])
                measurements[tool] = {"run": meta, "score": measured}
                if tool == "browcrawler":
                    measurements[tool]["collection"] = browser_collection(rows, expected, variant, origin)
                print(name, tool, "server-confirmed", len(measured["server_confirmed"]), "/", len(expected), flush=True)
            summary["variants"][variant] = measurements
            save(out / "summary.json", summary)
    finally:
        server.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
