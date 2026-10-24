import { resolve } from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { "/api": process.env.HEADSUP_BACKEND ?? "http://127.0.0.1:8000" },
    // The phone page is reached through a tunnel, since geolocation needs HTTPS.
    allowedHosts: [".trycloudflare.com"],
  },
  build: {
    rollupOptions: {
      input: { main: resolve(__dirname, "index.html"), phone: resolve(__dirname, "phone.html") },
    },
  },
});
