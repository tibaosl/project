import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 開發時前端跑在 Vite dev server（預設 5173），後端 FastAPI 跑在
// 127.0.0.1:8000；用 proxy 把 /api 轉發過去，瀏覽器端就不會碰到
// CORS 問題。正式環境不會用到這個 proxy——build 出來的 dist/ 直接被
// main.py 掛在同一個 origin 底下，/api 就是同源請求。
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
      "/files": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/setupTests.js",
    globals: true,
  },
});
