"use client";
import { useEffect, useState } from "react";

export default function ClientProbe({ mode }) {
  const [text, setText] = useState("waiting");
  useEffect(() => {
    const readLabel = (value) => value.length;
    readLabel("/api/matrix/decoy-not-a-request");
    async function run() {
      if (mode === "lazy") {
        const module = await import("./lazyProbe");
        await module.run();
      } else if (mode === "response") {
        const response = await fetch("/api/matrix/bootstrap");
        const data = await response.json();
        await fetch(data.next);
      } else if (mode === "svg") {
        const node = document.querySelector("svg");
        if (node instanceof SVGElement && node.className.baseVal === "probe")
          await fetch("/api/matrix/svg");
      } else {
        await fetch("/api/matrix/csr");
      }
      setText("complete");
    }
    void run();
  }, [mode]);
  return <section><svg className="probe" aria-hidden="true"><path d="M0 0L1 1" /></svg><p>{text}</p></section>;
}
