"use client";

import { useState } from "react";

export default function RuntimeAction() {
  const [result, setResult] = useState(null);

  async function trigger(event) {
    const button = event.currentTarget;
    if (!button.dataset.apiRoot || !button.dataset.apiName) return;
    const endpoint = `/${[button.dataset.apiRoot, button.dataset.apiName].join("/")}`;
    const response = await fetch(`${endpoint}?source=button`);
    setResult(await response.json());
  }

  return (
    <section>
      <button
        id="next-runtime-trigger"
        type="button"
        data-api-root="api"
        data-api-name="next-click"
        onClick={trigger}
      >
        Load runtime endpoint
      </button>
      <pre>{JSON.stringify(result)}</pre>
    </section>
  );
}
