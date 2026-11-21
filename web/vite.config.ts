import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { viteSingleFile } from "vite-plugin-singlefile";
export default defineConfig({ plugins: [react(), viteSingleFile()], build: { outDir: "../personal/static", emptyOutDir: true }, server: { port: 5174, proxy: { "/api": "http://127.0.0.1:8790", "/learning": "http://127.0.0.1:8790", "/novels": "http://127.0.0.1:8790", "/review": "http://127.0.0.1:8790", "/media": "http://127.0.0.1:8790" } } });
