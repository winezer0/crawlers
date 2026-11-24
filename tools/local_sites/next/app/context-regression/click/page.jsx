"use client";
export default function Page() {
  return <main><h1>Untriggered click remains a target</h1>
    <button onClick={(event) => {
        fetch("/api/context-regression/click", { headers: { "X-Component-Proof": "click:interaction" } });
    }}>Load details</button>
  </main>;
}
