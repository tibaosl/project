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
