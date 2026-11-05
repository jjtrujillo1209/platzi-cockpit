import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const API_PORT = process.env.KLIK_API_PORT || "8532";

export default defineConfig({
  plugins: [react()],
  server: {
    port: Number(process.env.KLIK_WEB_PORT) || 5183,
    proxy: {
      "/api": { target: `http://127.0.0.1:${API_PORT}`, changeOrigin: true },
      "/ws": { target: `ws://127.0.0.1:${API_PORT}`, ws: true },
    },
  },
});
