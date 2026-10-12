import { readEventStream } from "./chatStream";

/**
 * 呼叫 /api/login、/api/logout。登入成功只換回一個 session token，
 * 密碼登入成功後就不會再留在前端（不存 sessionStorage，也不會在之後的
 * 每一輪對話裡再送一次）。
 */
export async function requestLogin(username, password) {
  const res = await fetch("/api/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });

  let data;
  try {
    data = await res.json();
  } catch {
    throw new Error(`HTTP ${res.status}`);
  }

  if (!res.ok || data.status !== "success") {
    throw new Error(data.message || `登入失敗（HTTP ${res.status}）`);
  }

  return data.token;
}

/**
 * 呼叫 /api/login/chrome：後端會在這台電腦用 Chrome 登入 Portal，記得帳號時在看不到的視窗
 * 背景完成（約 5 秒），要使用者自己輸入密碼時才開看得到的視窗（最多等約 3 分鐘）。
 * 後端邊做邊回報進度（SSE），每一步的說明交給 onStatus 顯示。
 * 回傳 { token, username, chineseName }。
 */
export async function requestChromeLogin(onStatus = () => {}) {
  const res = await fetch("/api/login/chrome", { method: "POST" });
  if (!res.ok || !res.body) {
    throw new Error(`登入失敗（HTTP ${res.status}）`);
  }

  let result = null;
  let failure = "";
  await readEventStream(res.body, (event) => {
    if (event.type === "status") onStatus(event.text);
    else if (event.type === "result") result = event;
    else if (event.type === "error") failure = event.message;
  });

  if (!result) {
    throw new Error(failure || "登入失敗，請再試一次。");
  }
  return { token: result.token, username: result.username, chineseName: result.chinese_name || "" };
}

export async function requestLogout(token, username) {
  try {
    await fetch("/api/logout", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token, username }),
    });
  } catch {
    // 登出本來就是「盡量做」的收尾動作，連不上後端也不影響前端登出。
  }
}
