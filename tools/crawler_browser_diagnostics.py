"""Record passive execution errors and controlled prefetch evidence with Obscura.

This diagnostic uses one CDP connection (Obscura isolates connections), adds only
error listeners, and never supplies browser APIs or manufactured request URLs.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import time
from urllib.request import ProxyHandler, build_opener

import websocket

from run_local_site_regression import ROOT, sha256_file


ERROR_LISTENERS = r"""(()=>{
globalThis.__stageOneErrors=[];
addEventListener('error',e=>__stageOneErrors.push({kind:'error',message:e.message,file:e.filename,line:e.lineno,stack:e.error&&e.error.stack}));
addEventListener('unhandledrejection',e=>__stageOneErrors.push({kind:'rejection',message:String(e.reason),stack:e.reason&&e.reason.stack}));
})()"""

SNAPSHOT = r"""(()=>{
const anchors=Array.from(document.querySelectorAll('a[href]'));
const fiberNodes=Array.from(document.querySelectorAll('body *')).filter(n=>Object.keys(n).some(k=>k.startsWith('__reactFiber$')||k.startsWith('__reactProps$')));
return JSON.stringify({url:location.href,readyState:document.readyState,title:document.title,errors:globalThis.__stageOneErrors||[],
turboType:typeof globalThis.TURBOPACK,turboLength:Array.isArray(globalThis.TURBOPACK)?globalThis.TURBOPACK.length:null,
reactAttachedNodes:fiberNodes.length,anchors:anchors.length,deployment:document.documentElement.getAttribute('data-dpl-id'),
nuxtBuildId:globalThis.__NUXT__&&__NUXT__.config&&__NUXT__.config.app&&__NUXT__.config.app.buildId,
resources:typeof performance.getEntriesByType==='function'?performance.getEntriesByType('resource').filter(e=>e.name.includes('_rsc')||e.name.includes('/api/')).map(e=>({url:e.name,type:e.initiatorType})):[]});
})()"""


class CDP:
    def __init__(self, connection):
        self.connection = connection
        self.serial = 0
        self.events = []
        self.replies = {}
        self.started = time.monotonic()
        self.dropped_events = 0

    def receive(self):
        item = json.loads(self.connection.recv())
        if "id" in item:
            self.replies[item["id"]] = item
        elif len(self.events) < 10000:
            self.events.append({"elapsed_seconds": round(time.monotonic() - self.started, 6), **item})
        else:
            self.dropped_events += 1

    def call(self, method, params=None, session=None, timeout=45):
        self.serial += 1
        serial = self.serial
        message = {"id": serial, "method": method, "params": params or {}}
        if session:
            message["sessionId"] = session
        self.connection.send(json.dumps(message))
        deadline = time.monotonic() + timeout
        while serial not in self.replies:
            if time.monotonic() >= deadline:
                raise TimeoutError(method)
            self.connection.settimeout(min(1, max(0.01, deadline - time.monotonic())))
            try:
                self.receive()
            except websocket.WebSocketTimeoutException:
                continue
        return self.replies.pop(serial)

    def pump(self, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.connection.settimeout(min(0.2, max(0.01, deadline - time.monotonic())))
            try:
                self.receive()
            except websocket.WebSocketTimeoutException:
                pass


def value(reply):
    result = reply.get("result", {})
    if "exceptionDetails" in result or "error" in reply:
        return {"evaluation_error": result.get("exceptionDetails") or reply.get("error")}
    raw = result.get("result", {}).get("value")
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except ValueError:
            pass
    return raw


def summarize(events):
    requests, responses, failures, exceptions, console = [], [], [], [], []
    for item in events:
        method, params = item.get("method"), item.get("params", {})
        if method == "Network.requestWillBeSent":
            request = params.get("request", {})
            requests.append({"request_id": params.get("requestId"), "url": request.get("url"),
                             "method": request.get("method"), "headers": request.get("headers"),
                             "initiator": params.get("initiator"), "at": item["elapsed_seconds"]})
        elif method == "Network.responseReceived":
            response = params.get("response", {})
            responses.append({"request_id": params.get("requestId"), "url": response.get("url"),
                              "status": response.get("status"), "mime_type": response.get("mimeType")})
        elif method == "Network.loadingFailed":
            failures.append(params)
        elif method == "Runtime.exceptionThrown":
            exceptions.append(params.get("exceptionDetails", params))
        elif method in ("Runtime.consoleAPICalled", "Log.entryAdded"):
            console.append(params)
    unique = {(r['method'], r['url']): r for r in requests}
    return {"requests": requests, "responses": responses, "network_failures": failures,
            "exceptions": exceptions, "console": console,
            "rsc_requests": [r for r in requests if "_rsc=" in (r.get("url") or "")],
            "unique_rsc_urls": sorted({r['url'] for r in unique.values() if '_rsc=' in (r['url'] or '')})}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--proxy", help="optional existing HTTP proxy; otherwise direct transport")
    parser.add_argument("--ca-file", type=Path)
    parser.add_argument("--hover-href", default="/docs/app/getting-started/installation")
    parser.add_argument("--click", action="store_true", help="click the observed same-origin link after passive samples")
    args = parser.parse_args()
    out = args.out_dir.resolve()
    if out.exists():
        parser.error("Use a new diagnostic directory")
    out.mkdir(parents=True)
    binary = ROOT / "browcrawler/third/obscura/obscura.exe"
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    env = {k: v for k, v in os.environ.items() if k.lower() not in ("http_proxy", "https_proxy", "all_proxy")}
    env["OBSCURA_NAV_TIMEOUT_MS"] = "45000"
    if args.ca_file:
        env["SSL_CERT_FILE"] = str(args.ca_file.resolve())
    command = [str(binary), "serve", "--port", str(port), "--stealth", "--allow-private-network",
               "--storage-dir", str(out / "profile")]
    if args.proxy:
        command += ["--proxy", args.proxy]
    save = lambda name, data: (out / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    save("command.json", {"command": command, "binary_sha256": sha256_file(binary), "url": args.url,
                          "transport": "proxy" if args.proxy else "direct", "production_crawler_modified": False})
    log = (out / "obscura.log").open("wb")
    process = subprocess.Popen(command, cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                               creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    cdp = None
    snapshots, commands = [], []
    start = time.monotonic()
    try:
        opener = build_opener(ProxyHandler({}))
        deadline = time.monotonic() + 20
        while True:
            try:
                with opener.open(f"http://127.0.0.1:{port}/json/version", timeout=1) as response:
                    version = json.load(response)
                break
            except OSError:
                if process.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError("Obscura did not become ready")
                time.sleep(0.05)
        save("version.json", version)
        connection = websocket.create_connection(version["webSocketDebuggerUrl"], timeout=1, suppress_origin=True,
                                                 http_proxy_host=None)
        cdp = CDP(connection)
        target = cdp.call("Target.createTarget", {"url": "about:blank"})
        commands.append(target)
        attached = cdp.call("Target.attachToTarget", {"targetId": target["result"]["targetId"], "flatten": True})
        commands.append(attached)
        session = attached["result"]["sessionId"]
        for method in ("Page.enable", "Runtime.enable", "Network.enable", "Log.enable"):
            commands.append({"method": method, "reply": cdp.call(method, session=session)})
        commands.append(cdp.call("Page.addScriptToEvaluateOnNewDocument", {"source": ERROR_LISTENERS}, session))
        navigation = cdp.call("Page.navigate", {"url": args.url}, session, timeout=60)
        commands.append(navigation)
        if "error" in navigation or navigation.get("result", {}).get("errorText"):
            raise RuntimeError("Navigation failed: " + json.dumps(navigation, ensure_ascii=False))
        for seconds in (2, 8, 15):
            cdp.pump(seconds)
            snapshots.append({"phase": "passive", "elapsed_seconds": time.monotonic() - start,
                              "state": value(cdp.call("Runtime.evaluate", {"expression": SNAPSHOT, "returnByValue": True}, session))})
            print(json.dumps(snapshots[-1], ensure_ascii=False), flush=True)
        selector = "a[href=" + json.dumps(args.hover_href) + "]"
        expression = "(()=>{const a=document.querySelector(" + json.dumps(selector) + ");if(!a)return JSON.stringify({missing:true});a.scrollIntoView();const r=a.getBoundingClientRect();return JSON.stringify({href:a.href,x:r.x+r.width/2,y:r.y+r.height/2,width:r.width,height:r.height});})()"
        target_box = value(cdp.call("Runtime.evaluate", {"expression": expression, "returnByValue": True}, session))
        save("interaction-target.json", target_box)
        if isinstance(target_box, dict) and target_box.get("width", 0) > 0 and target_box.get("height", 0) > 0:
            commands.append(cdp.call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": target_box["x"], "y": target_box["y"]}, session))
            cdp.pump(5)
        snapshots.append({"phase": "after-hover", "state": value(cdp.call("Runtime.evaluate", {"expression": SNAPSHOT, "returnByValue": True}, session))})
        if args.click:
            expression = "(()=>{const a=document.querySelector(" + json.dumps(selector) + ");if(a&&new URL(a.href).origin===location.origin){a.click();return true;}return false;})()"
            commands.append(cdp.call("Runtime.evaluate", {"expression": expression}, session))
            cdp.pump(10)
            snapshots.append({"phase": "after-click", "state": value(cdp.call("Runtime.evaluate", {"expression": SNAPSHOT, "returnByValue": True}, session))})
    except Exception as error:
        save("failure.json", {"type": type(error).__name__, "message": str(error)})
        raise
    finally:
        if cdp:
            save("events.json", cdp.events)
            result = summarize(cdp.events)
            result.update(snapshots=snapshots, commands=commands, dropped_events=cdp.dropped_events,
                          elapsed_seconds=time.monotonic() - start, transport="proxy" if args.proxy else "direct")
            save("summary.json", result)
            cdp.connection.close()
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        log.close()
    print(out)


if __name__ == "__main__":
    main()
