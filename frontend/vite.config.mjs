import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const proxy = {
  "^/api(?:/|\\?|$)": {
    target: process.env.MDE_API_TARGET || "http://127.0.0.1:9090",
  },
};

export default defineConfig({
  base: "/",
  build: {
    outDir: "dist/client",
  },
  optimizeDeps: {
    include: ["react", "react-dom/client"],
  },
  preview: {
    port: 5173,
    strictPort: true,
    proxy,
    host: "0.0.0.0",
    allowedHosts: ["mdeng-ai.adople.in", "terminal.local"],
  },
  server: {
    port: 5173,
    strictPort: true,
    proxy,
    host: "0.0.0.0",
    allowedHosts: ["mdeng-ai.adople.in", "terminal.local"],
    warmup: {
      clientFiles: ["./src/main.tsx"],
    },
  },
  plugins: [react()],
});
