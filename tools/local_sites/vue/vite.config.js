import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

function localApiFixture() {
  return {
    name: "local-vue-api-fixture",
    configurePreviewServer(server) {
      server.middlewares.use((request, response, next) => {
        const url = new URL(request.url || "/", "http://127.0.0.1");
        if (request.method === "GET" && url.pathname === "/api/vue-catalog") {
          response.statusCode = 200;
          response.setHeader("content-type", "application/json; charset=utf-8");
          response.end(JSON.stringify({ items: ["vue-one", "vue-two"] }));
          return;
        }
        if (request.method === "GET" && url.pathname === "/api/vue-click") {
          response.statusCode = 200;
          response.setHeader("content-type", "application/json; charset=utf-8");
          response.end(JSON.stringify({ route: url.pathname, source: url.searchParams.get("source") }));
          return;
        }
        if (request.method === "POST" && url.pathname === "/api/vue-search") {
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
  plugins: [vue(), localApiFixture()],
  build: { sourcemap: true },
  preview: { strictPort: true },
});
