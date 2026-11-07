import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import { mkdirSync, copyFileSync } from "node:fs";
import { resolve } from "node:path";
export default defineConfig(({ mode }) => {
  const local = loadEnv(mode, resolve(".."), "MODELDock_");
  const apiPort = process.env.MODELDock_PORT || local.MODELDock_PORT || "8000";
  const apiTarget = `http://127.0.0.1:${apiPort}`;
  return {
    plugins: [
      react(),
      {
        name: "offline-swagger",
        closeBundle() {
          mkdirSync(resolve("dist/swagger"), { recursive: true });
          for (const file of ["swagger-ui-bundle.js", "swagger-ui.css"])
            copyFileSync(
              resolve("node_modules/swagger-ui-dist", file),
              resolve("dist/swagger", file),
            );
        },
      },
    ],
    server: {
      port: Number(
        process.env.MODELDock_WEB_PORT || local.MODELDock_WEB_PORT || "5173",
      ),
      strictPort: true,
      proxy: {
        "/api": apiTarget,
        "/v1": apiTarget,
        "/health": apiTarget,
        "/docs": apiTarget,
        "/openapi.json": apiTarget,
        "/swagger": apiTarget,
      },
    },
    build: {
      rollupOptions: {
        output: {
          manualChunks: {
            react: ["react", "react-dom", "react-router-dom"],
            ui: ["antd", "@ant-design/icons"],
            query: ["@tanstack/react-query", "i18next", "react-i18next"],
          },
        },
      },
    },
  };
});
