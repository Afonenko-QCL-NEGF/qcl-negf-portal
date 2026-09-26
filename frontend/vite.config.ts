import { defineConfig } from "vite";

export default defineConfig({
  build: { outDir: "../src/qcl_negf_api/static", emptyOutDir: true },
  server: { proxy: { "/api": "http://127.0.0.1:8080" } },
});
