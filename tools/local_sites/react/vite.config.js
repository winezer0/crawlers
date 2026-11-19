import { defineConfig } from "vite";

function localApiFixture() {
  return {
    name: "local-api-fixture",
    configurePreviewServer(server) {
      server.middlewares.use((request, response, next) => {
        const url = new URL(request.url || "/", "http://127.0.0.1");
        if (request.method === "GET" && url.pathname === "/api/react-click") {
          response.statusCode = 200;
          response.setHeader("content-type", "application/json; charset=utf-8");
          response.end(JSON.stringify({ route: url.pathname, source: url.searchParams.get("source") }));
          return;
        }
        if (request.method === "GET" && ["menu", "scroll", "page"].some((name) => url.pathname === `/api/react-${name}`)) {
          response.statusCode = 200;
          response.setHeader("content-type", "application/json; charset=utf-8");
          response.end(JSON.stringify({ route: url.pathname, page: Number(url.searchParams.get("page") || 1) }));
          return;
        }
        if (request.method === "POST" && url.pathname === "/api/react-search") {
          const chunks = [];
          request.on("data", (chunk) => chunks.push(chunk));
          request.on("end", () => {
            response.statusCode = 200;
            response.setHeader("content-type", "application/json; charset=utf-8");
            response.end(JSON.stringify({
              route: url.pathname,
              method: request.method,
              received: Buffer.concat(chunks).toString("utf8"),
            }));
          });
          return;
        }
        next();
      });
    },
  };
}

export default defineConfig({
  plugins: [localApiFixture()],
  esbuild: { jsx: "automatic" },
  build: { sourcemap: true },
  preview: { strictPort: true },
});
