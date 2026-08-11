import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";

export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: "autoUpdate",
      includeAssets: ["favicon.svg"],
      manifest: {
        name: "Kitchen Pass",
        short_name: "Kitchen Pass",
        description: "Pickleball tournament manager and scorekeeper",
        theme_color: "#0E7C6B",
        background_color: "#f6f7f3",
        display: "standalone",
        start_url: "/",
        icons: [
          { src: "icon-192.png", sizes: "192x192", type: "image/png" },
          { src: "icon-512.png", sizes: "512x512", type: "image/png" },
        ],
      },
      workbox: {
        // Precache the shell so the scorekeeper can cold-start with no signal.
        globPatterns: ["**/*.{js,css,html,svg,png,woff2}"],
        navigateFallback: "index.html",
        runtimeCaching: [
          {
            // Reference data (players, draws) is fine slightly stale offline.
            urlPattern: /\/api\/v1\/(players|tournaments|divisions)/,
            handler: "NetworkFirst",
            options: {
              cacheName: "kp-api",
              networkTimeoutSeconds: 4,
              expiration: { maxEntries: 200, maxAgeSeconds: 60 * 60 * 24 },
            },
          },
        ],
      },
      devOptions: { enabled: false },
    }),
  ],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true, ws: true },
    },
  },
  test: {
    globals: true,
    // jsdom throughout: the render tests need a DOM, and the conformance suite
    // still gets real Node APIs (fs, path) because vitest runs in Node with
    // jsdom globals layered on.
    environment: "jsdom",
    // jsdom defaults to about:blank, which is an opaque origin — localStorage
    // does not exist there and every auth-touching component throws.
    environmentOptions: { jsdom: { url: "http://localhost:5173" } },
    setupFiles: ["./tests/setup.ts"],
    include: ["tests/**/*.test.{ts,tsx}"],
    // e2e/ is Playwright's, driven by `npm run e2e`.
    exclude: ["e2e/**", "node_modules/**", "dist/**"],
  },
});
