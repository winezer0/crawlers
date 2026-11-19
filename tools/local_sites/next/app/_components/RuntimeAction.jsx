"use client";

import { useEffect, useRef, useState } from "react";

export default function RuntimeAction() {
  const [result, setResult] = useState(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [page, setPage] = useState(1);
  const scrollRequested = useRef(false);

  async function requestInteraction(elementID, suffix = "") {
    const target = document.getElementById(elementID);
    if (!target?.dataset.apiRoot || !target.dataset.apiName) return;
    const endpoint = `/${target.dataset.apiRoot}/${target.dataset.apiName}`;
    const response = await fetch(`${endpoint}${suffix}`);
    setResult(await response.json());
  }

  useEffect(() => {
    const onScroll = () => {
      const root = document.scrollingElement || document.documentElement;
      if (!scrollRequested.current && window.scrollY + window.innerHeight >= root.scrollHeight - 20) {
        scrollRequested.current = true;
        void requestInteraction("next-scroll-trigger");
      }
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

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
      <button id="next-menu-open" type="button" aria-haspopup="true" onClick={() => setMenuOpen(true)}>
        Open menu
      </button>
      {menuOpen && (
        <button id="next-menu-load" type="button" data-api-root="api" data-api-name="next-menu" onClick={() => requestInteraction("next-menu-load")}>
          Load menu data
        </button>
      )}
      <button
        id="next-page-next"
        type="button"
        data-api-root="api"
        data-api-name="next-page"
        disabled={page > 1}
        onClick={() => {
          const nextPage = page + 1;
          setPage(nextPage);
          void requestInteraction("next-page-next", `?page=${nextPage}`);
        }}
      >
        Load next page
      </button>
      <p>Current page: {page}</p>
      <pre>{JSON.stringify(result)}</pre>
      <span id="next-scroll-trigger" hidden data-api-root="api" data-api-name="next-scroll" />
      <div aria-hidden="true" style={{ height: 1400 }} />
    </section>
  );
}
