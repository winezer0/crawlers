"""Verify complete, snapshot-bound offline HTTP response inputs."""
import hashlib
import json
from pathlib import Path


def sha(body):
    return hashlib.sha256(body).hexdigest()


def response_inputs(manifest, document, frontend_sha, dataset, scoped_url, max_file, max_total):
    if not manifest:
        return [], None
    manifest = Path(manifest)
    raw = manifest.read_bytes()
    data = json.loads(raw)
    if data.get("document_url") != document or data.get("frontend_sha256") != frontend_sha:
        raise ValueError("Response manifest belongs to a different document snapshot")
    records, seen, total = [], set(), 0
    for item in data["responses"]:
        method = item.get("method", "GET")
        url = scoped_url(item["url"], document, document)
        if method not in ("GET", "HEAD") or item.get("truncated") or not 200 <= item["status"] <= 599:
            raise ValueError("Invalid or incomplete response input")
        path = (manifest.parent / item["file"]).resolve()
        if not path.is_relative_to(manifest.parent.resolve()):
            raise ValueError("Response body escapes manifest directory")
        body = path.read_bytes()
        if sha(body) != item["sha256"] or len(body) > max_file:
            raise ValueError("Response body hash changed or byte limit exceeded")
        request_headers = item.get("request_headers", {})
        if not all(isinstance(k, str) and isinstance(v, str) for k, v in request_headers.items()):
            raise ValueError("Request headers must contain strings")
        normalized = {k.lower(): v for k, v in request_headers.items()}
        if len(normalized) != len(request_headers):
            raise ValueError("Duplicate case-insensitive request header")
        key = json.dumps([method, url, normalized], sort_keys=True)
        if key in seen:
            raise ValueError("Duplicate response identity")
        seen.add(key)
        total += len(body)
        if total > max_total or len(records) >= 512:
            raise ValueError("Response input limit exceeded")
        relative = "responses/" + sha(key.encode()) + ".body"
        target = dataset / relative
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(body)
        records.append(dict(item, method=method, url=url, file=str(target.resolve())))
    return records, {"path": str(manifest.resolve()), "sha256": sha(raw), "responses": len(records), "bytes": total}
