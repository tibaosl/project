// main.py 用 StaticFiles(html=True) 掛載 dist/：它只會在剛好有 404.html
// 存在時才拿來當作「找不到對應檔案的路徑」的 fallback（見 starlette
// staticfiles.py 的 get_response），不會自動把所有路徑導回 index.html。
// 我們是純前端路由的 SPA（react-router 的 /login、/chat、/oauth-complete
// 都不是 dist/ 底下真的存在的檔案），沒有這個 fallback 的話，直接用網址
// 打開這些路徑（例如 Portal OAuth 登入完成後被導回 /oauth-complete）在
// 正式環境會直接 404，不會進到 React app。
import { copyFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const distDir = path.resolve(fileURLToPath(new URL("../dist", import.meta.url)));
copyFileSync(path.join(distDir, "index.html"), path.join(distDir, "404.html"));
console.log("[postbuild] 已複製 dist/index.html -> dist/404.html（給 StaticFiles(html=True) 當 SPA fallback 用）");
