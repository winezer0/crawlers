"use client";
import { useState } from "react";

export default function Page() {
  const [open, setOpen] = useState(false);
  return <main><h1>Unlock an observed interaction</h1>
    <section hidden><button onClick={() => fetch("/api/context-regression/state-hidden")}>Hidden</button></section>
    <section inert><button onClick={() => fetch("/api/context-regression/state-inert")}>Inert</button></section>
    <section style={{ display: "none" }}><button onClick={() => fetch("/api/context-regression/state-display")}>No display</button></section>
    <section style={{ visibility: "hidden" }}><button onClick={() => fetch("/api/context-regression/state-visibility")}>Invisible</button></section>
    <section style={{ pointerEvents: "none" }}><button onClick={() => fetch("/api/context-regression/state-pointer")}>No pointer</button></section>
    <fieldset disabled={!open} style={{ display: open ? "block" : "none" }}>
      <button onClick={() => fetch("/api/context-regression/state", {
        headers: { "X-Component-Proof": open ? "state:unlocked" : "state:premature" }
      })}>Load after unlock</button>
    </fieldset>
    <button onClick={() => Promise.resolve().then(() => setOpen(true))}>Unlock</button>
  </main>;
}
