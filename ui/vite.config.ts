import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In dev, API calls go to the Python server (pdf-tagfix-ui) on port 8000.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
});
