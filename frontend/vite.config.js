import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In local dev, proxy /api to the FastAPI backend so the frontend and API share
// an origin (no CORS headaches). In Docker, nginx does the same proxying.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.VITE_API_TARGET || "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
