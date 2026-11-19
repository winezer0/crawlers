import { createReadStream, existsSync, statSync } from "node:fs";
import { createServer } from "node:http";
import path from "node:path";

const fixture = process.env.CRAWL_FIXTURE_NAME;
const root = path.resolve(process.cwd(), process.env.CRAWL_FIXTURE_STATIC_DIR || "dist");
const portIndex = process.argv.indexOf("--port");
const port = Number(process.env.PORT || (portIndex >= 0 ? process.argv[portIndex + 1] : 0));
const apiPrefix = `/api/${fixture}-`;

function sendJSON(response, status, body) {
  response.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "cache-control": "no-store",
  });
  response.end(JSON.stringify(body));
}

async function readBody(request) {
  const chunks = [];
  for await (const chunk of request) chunks.push(chunk);
  return Buffer.concat(chunks).toString("utf8").slice(0, 1024 * 1024);
}

function contentType(file) {
  const extension = path.extname(file).toLowerCase();
  return ({
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".map": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".woff2": "font/woff2",
  })[extension] || "application/octet-stream";
}

function findStaticFile(rawPath) {
  let decoded;
  try {
    decoded = decodeURIComponent(rawPath);
  } catch {
    return null;
  }
  const relative = decoded.replace(/^\/+/, "");
  const candidate = path.resolve(root, relative || "index.html");
  if (candidate !== root && !candidate.startsWith(root + path.sep)) return null;

  const choices = [candidate, `${candidate}.html`, path.join(candidate, "index.html")];
  for (const choice of choices) {
    if (existsSync(choice) && statSync(choice).isFile()) return choice;
  }
  return null;
}

const server = createServer(async (request, response) => {
  const url = new URL(request.url || "/", `http://127.0.0.1:${port}`);
  if (url.pathname.startsWith(apiPrefix)) {
    const action = url.pathname.slice(apiPrefix.length);
    if (action === "search" && request.method === "POST") {
      sendJSON(response, 200, { route: url.pathname, method: request.method, received: await readBody(request) });
      return;
    }
    if (request.method === "GET" && ["catalog", "click", "menu", "scroll", "page", "shadow", "frame"].includes(action)) {
      sendJSON(response, 200, {
        route: url.pathname,
        source: url.searchParams.get("source"),
        page: Number(url.searchParams.get("page") || 1),
        items: action === "catalog" ? [`${fixture}-one`, `${fixture}-two`] : undefined,
      });
      return;
    }
    sendJSON(response, 404, { route: url.pathname, error: "fixture API not found" });
    return;
  }
  if (url.pathname.startsWith("/api/")) {
    sendJSON(response, 404, { route: url.pathname, error: "fixture API not found" });
    return;
  }

  let file = findStaticFile(url.pathname);
  if (!file && fixture === "angular" && url.pathname === "/angular/guide") {
    file = findStaticFile("/");
  }
  if (!file) {
    response.writeHead(404, { "content-type": "text/plain; charset=utf-8" });
    response.end("not found");
    return;
  }
  response.writeHead(200, { "content-type": contentType(file), "cache-control": "no-store" });
  createReadStream(file).pipe(response);
});

server.listen(port, "127.0.0.1", () => {
  const address = server.address();
  console.log(`${fixture} static fixture listening on 127.0.0.1:${address.port}`);
});
