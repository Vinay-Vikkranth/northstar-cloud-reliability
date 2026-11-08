import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const apiProxy = process.env.CIR_API_PROXY || "http://127.0.0.1:8101";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 8100,
    strictPort: true,
    proxy: {
      "/api": apiProxy,
      "/health": apiProxy,
    },
  },
  preview: {
    host: "127.0.0.1",
    port: 8100,
    strictPort: true,
    proxy: {
      "/api": apiProxy,
      "/health": apiProxy,
    },
  },
});
