import { svelte } from "@sveltejs/vite-plugin-svelte";
import { resolve } from "node:path";
import { defineConfig } from "vite";

// Zwei Seiten in einer App: index.html (Orb-Overlay) und hud.html (Dashboard).
// Gebaut wird nach orb/dist – von dort bettet Tauri die Oberfläche ein (tauri.conf.json: frontendDist).
export default defineConfig({
  root: "ui",
  base: "./", // relative Pfade: läuft in Tauri und direkt als Datei im Browser (?preview / ?demo)
  plugins: [svelte({ configFile: resolve(import.meta.dirname, "svelte.config.js") })],
  build: {
    outDir: "../dist",
    emptyOutDir: true,
    target: ["es2021", "safari15"],
    chunkSizeWarningLimit: 900, // three.js
    rollupOptions: {
      input: {
        index: resolve(import.meta.dirname, "ui/index.html"),
        hud: resolve(import.meta.dirname, "ui/hud.html"),
      },
    },
  },
});
