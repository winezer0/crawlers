import { lazy, Suspense, useEffect, useState } from "react";
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

  useEffect(() => {
    fetch("/api/react-catalog")
      .then((response) => response.json())
      .then(setCatalog)
      .catch(() => setCatalog({ items: [] }));

    const onPopState = () => setPath(window.location.pathname);
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  function navigateToGuide() {
    window.history.pushState({}, "", "/react/guide");
    setPath("/react/guide");
  }

  return (
    <main>
      <h1>Local React crawl fixture</h1>
      <p>Current client route: {path}</p>
      <button type="button" onClick={navigateToGuide}>Open guide</button>
      <pre>{JSON.stringify(catalog)}</pre>
      <Suspense fallback={<p>Loading lazy panel</p>}>
        <LazyPanel />
      </Suspense>
    </main>
  );
}

createRoot(document.getElementById("root")).render(<App />);
