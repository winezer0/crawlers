"""Pack or restore the selected frozen evidence for a Windows workstation move."""
import argparse
import hashlib
import json
from pathlib import Path
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "handoff/20261126"
RUNS = (
    "enhanced-acceptance-20261126", "katana-base-final-20261126",
    "runtime-proof-accepted-20261126", "runtime-proof-body-20261126",
    "framework-seed-holdouts-20261126", "next-independent-20261126",
    "next-context-20261126", "katana-discovery-final-20261126",
    "katana-enhanced-baselines", "next-rsc-evidence-20261123-v1",
    "next-docs-runtime-dataset-20261123", "next-docs-layout-removal-20261124-final",
    "bounded-execution-production-20261124",
)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def selected_files():
    roots = [ROOT / "artifacts/crawler-comparison" / run for run in RUNS]
    roots += [ROOT / "tools/local_sites" / site / build for site, build in
              (("nuxt", ".output"), ("next", ".next"), ("react", "dist"))]
    for directory in roots:
        if not directory.is_dir():
            raise RuntimeError(f"Missing required evidence: {directory}")
        for path in sorted(directory.rglob("*")):
            if path.is_file() and path.suffix.lower() != ".exe":
                yield path
    # Preserve matching browser and reference executables for cache validation.
    for relative in ("othercrawlers/katana.exe",
                     "artifacts/crawler-comparison/katana-discovery-final-20261126/browcrawler.exe",
                     "artifacts/crawler-comparison/katana-base-final-20261126/basecrawler.exe"):
        path = ROOT / relative
        if not path.is_file():
            raise RuntimeError(f"Missing binary: {path}")
        yield path


def pack():
    BUNDLE.mkdir(parents=True, exist_ok=True)
    if (BUNDLE / "manifest.json").exists():
        raise RuntimeError("Bundle already exists; do not overwrite frozen data")
    records = []
    with tempfile.TemporaryFile() as temporary:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for path in selected_files():
                relative = path.relative_to(ROOT).as_posix()
                data = path.read_bytes()
                archive.writestr(relative, data)
                records.append({"path": relative, "bytes": len(data), "sha256": digest(data)})
        temporary.seek(0)
        parts = []
        while block := temporary.read(40 * 1024 * 1024):
            name = f"evidence.zip.part{len(parts) + 1:03d}"
            (BUNDLE / name).write_bytes(block)
            parts.append({"path": name, "bytes": len(block), "sha256": digest(block)})
    manifest = {"source_root": str(ROOT), "parts": parts, "files": records,
                "note": "Historical source/binary hashes remain historical; latest enumeration fix awaits end-to-end verification."}
    (BUNDLE / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Packed {len(records)} files into {len(parts)} parts", flush=True)


def restore(verify_only=False, relocate=False):
    manifest = json.loads((BUNDLE / "manifest.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryFile() as temporary:
        for part in manifest["parts"]:
            data = (BUNDLE / part["path"]).read_bytes()
            if digest(data) != part["sha256"]:
                raise RuntimeError(f"Corrupt bundle part: {part['path']}")
            temporary.write(data)
        temporary.seek(0)
        with zipfile.ZipFile(temporary) as archive:
            for record in manifest["files"]:
                target = (ROOT / record["path"]).resolve()
                if not target.is_relative_to(ROOT):
                    raise RuntimeError("Unsafe archive path")
                data = archive.read(record["path"])
                if digest(data) != record["sha256"]:
                    raise RuntimeError(f"Corrupt evidence: {record['path']}")
                if not verify_only:
                    if target.exists() and digest(target.read_bytes()) != record["sha256"]:
                        raise RuntimeError(f"Refusing to overwrite differing local data: {target}")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
    if relocate and not verify_only:
        old = manifest["source_root"]
        replacements = ((old.replace("\\", "/"), ROOT.as_posix()), (old, str(ROOT)))

        def convert(value):
            if isinstance(value, str):
                for source, destination in replacements:
                    value = value.replace(source, destination)
                return value
            if isinstance(value, list):
                return [convert(item) for item in value]
            if isinstance(value, dict):
                return {convert(key): convert(item) for key, item in value.items()}
            return value

        count = 0
        for record in manifest["files"]:
            path = ROOT / record["path"]
            # Never mutate raw JSONL requests, response files or frozen verification.
            if path.name not in {"summary.json", "metadata.json", "comparison.json"}:
                continue
            before = path.read_bytes()
            parsed = json.loads(before)
            after = convert(parsed)
            if after != parsed:
                path.with_suffix(path.suffix + ".original").write_bytes(before)
                path.write_text(json.dumps(after, ensure_ascii=False, indent=2), encoding="utf-8")
                count += 1
        print(f"Relocated {count} metadata files; original bytes retained as .json.original")
    print(f"Verified {len(manifest['files'])} frozen files; restored={not verify_only}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("pack", "restore", "verify"))
    parser.add_argument("--relocate", action="store_true", help="derive local metadata paths, preserving original evidence")
    args = parser.parse_args()
    if args.action == "pack":
        pack()
    else:
        restore(args.action == "verify", args.relocate)
