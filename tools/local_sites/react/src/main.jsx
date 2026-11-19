import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";

const LazyPanel = lazy(() => import("./LazyPanel.jsx"));

function readEditorToken(token) {
  return token;
}

function renderPreview(markup) {
  return markup;
}

const nonRequestExamples = [
  "accessibility.signals.chatRequestSent",
  "editor.background",
  "workbench.hover.pointer",
  "a.action-label",
  "starting-nuxt-server",
  "<span>example</span>",
];

for (const token of nonRequestExamples) {
  readEditorToken(token);
}

renderPreview("<span>rendered as text, not a route</span>");

function App() {
  const [catalog, setCatalog] = useState(null);
  const [path, setPath] = useState(window.location.pathname);
  const [clickResult, setClickResult] = useState(null);
  const [interactionResult, setInteractionResult] = useState(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [page, setPage] = useState(1);
  const scrollRequested = useRef(false);

  async function requestInteraction(elementID, suffix = "") {
    const target = document.getElementById(elementID);
    if (!target?.dataset.apiRoot || !target.dataset.apiName) return;
    const endpoint = `/${target.dataset.apiRoot}/${target.dataset.apiName}`;
    const response = await fetch(`${endpoint}${suffix}`);
    setInteractionResult(await response.json());
  }

  useEffect(() => {
    fetch("/api/react-catalog")
      .then((response) => response.json())
      .then(setCatalog)
      .catch(() => setCatalog({ items: [] }));

    const onPopState = () => setPath(window.location.pathname);
    const onScroll = () => {
      const root = document.scrollingElement || document.documentElement;
      if (!scrollRequested.current && window.scrollY + window.innerHeight >= root.scrollHeight - 20) {
        scrollRequested.current = true;
        void requestInteraction("react-scroll-trigger");
      }
    };
    window.addEventListener("popstate", onPopState);
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      window.removeEventListener("popstate", onPopState);
      window.removeEventListener("scroll", onScroll);
    };
  }, []);

  function navigateToGuide() {
    window.history.pushState({}, "", "/react/guide");
    setPath("/react/guide");
  }

  async function triggerRuntimeRequest() {
    const button = document.getElementById("react-runtime-trigger");
    if (!button?.dataset.apiRoot || !button.dataset.apiName) return;
    const endpoint = `/${button.dataset.apiRoot}/${button.dataset.apiName}`;
    const response = await fetch(`${endpoint}?source=button`);
    setClickResult(await response.json());
  }

  return (
    <main>
      <h1>Local React crawl fixture</h1>
      <p>Current client route: {path}</p>
      <button type="button" onClick={navigateToGuide}>Open guide</button>
      <button
        id="react-runtime-trigger"
        type="button"
        data-api-root="api"
        data-api-name="react-click"
        onClick={triggerRuntimeRequest}
      >
        Load runtime endpoint
      </button>
      <button id="react-menu-open" type="button" aria-haspopup="true" onClick={() => setMenuOpen(true)}>
        Open menu
      </button>
      {menuOpen && (
        <button id="react-menu-load" type="button" data-api-root="api" data-api-name="react-menu" onClick={() => requestInteraction("react-menu-load")}>
          Load menu data
        </button>
      )}
      <button
        id="react-page-next"
        type="button"
        data-api-root="api"
        data-api-name="react-page"
        disabled={page > 1}
        onClick={() => {
          const nextPage = page + 1;
          setPage(nextPage);
          void requestInteraction("react-page-next", `?page=${nextPage}`);
        }}
      >
        Load next page
      </button>
      <p>Current page: {page}</p>
      <pre>{JSON.stringify(clickResult)}</pre>
      <pre>{JSON.stringify(interactionResult)}</pre>
      <pre>{JSON.stringify(catalog)}</pre>
      <span id="react-scroll-trigger" hidden data-api-root="api" data-api-name="react-scroll" />
      <Suspense fallback={<p>Loading lazy panel</p>}>
        <LazyPanel />
      </Suspense>
      <div aria-hidden="true" style={{ height: 1400 }} />
    </main>
  );
}

createRoot(document.getElementById("root")).render(<App />);
