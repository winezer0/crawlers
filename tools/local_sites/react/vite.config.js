import { defineConfig } from "vite";

export default defineConfig({
  esbuild: { jsx: "automatic" },
  build: { sourcemap: true },
  preview: { strictPort: true },
});
