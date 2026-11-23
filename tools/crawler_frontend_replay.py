"""Replay downloaded HTML through the current Go parser, with no HTTP requests."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GO_HELPER = r'''
package main
import (
 "encoding/json"
 "fmt"
 "net/url"
 "os"
 "github.com/winezer0/basecrawler/parse"
 "github.com/winezer0/basecrawler/frontier"
)
type Input struct { File string; URL string }
type Output struct { File string `json:"file"`; URL string `json:"url"`; Method string `json:"method"`; Source string `json:"source"`; Confidence string `json:"confidence"`; Evidence string `json:"evidence"`; Kind string `json:"kind"` }
func main(){
 var inputs []Input
 if err:=json.NewDecoder(os.Stdin).Decode(&inputs);err!=nil {panic(err)}
 out:=[]Output{}
 for _,input:=range inputs {
  body,err:=os.ReadFile(input.File);if err!=nil{panic(err)}
  parent,err:=url.Parse(input.URL);if err!=nil{panic(err)}
  requests,err:=parse.ParseHTML(body,parse.Options{});if err!=nil{panic(err)}
  for _,r:=range requests {
   target,ok:=frontier.Normalize(r.Raw,parent);if !ok {continue}
   out=append(out,Output{input.File,target.String(),r.Method,r.Source,r.Confidence,r.Evidence,r.Kind})
  }
 }
 if err:=json.NewEncoder(os.Stdout).Encode(out);err!=nil{fmt.Fprintln(os.Stderr,err);os.Exit(1)}
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frontend-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    go = shutil.which("go")
    if not go:
        parser.error("Go is required to use the current parser")
    inputs = []
    for path in sorted(args.frontend_dir.glob("*.html")):
        metadata = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
        if "text/html" not in metadata.get("content_type", ""):
            continue
        inputs.append({"File": str(path.resolve()), "URL": metadata["url"]})
    if not inputs:
        parser.error("No downloaded HTML with content_type metadata")
    with tempfile.TemporaryDirectory(prefix="crawler-frontend-replay-") as temporary:
        helper = Path(temporary) / "replay.go"
        helper.write_text(GO_HELPER, encoding="utf-8")
        completed = subprocess.run([go, "run", str(helper)], cwd=ROOT / "basecrawler",
                                   input=json.dumps(inputs), text=True, capture_output=True,
                                   encoding="utf-8", check=True, timeout=120)
    rows = json.loads(completed.stdout)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False)+"\n")
    print(json.dumps({"documents": len(inputs), "source_edges": len(rows), "network_requests": 0}))


if __name__ == "__main__":
    main()
