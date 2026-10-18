/**
 * 呼叫 /api/reminders 取得開場畫面的「最近要注意」（後端 backend/analysis/reminders.py）。
 * 用 POST 帶 token，不放在網址上。沒登入只有校曆。
 * 回傳 { title, range, logged_in, status, loading, items, notes }，status 是 "loading" 時
 * 代表登入後的背景準備還沒做完，過幾秒再問一次。
 */
export async function fetchReminders(token) {
  const res = await fetch("/api/reminders", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token: token || "" }),
  });
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`);
  }
  const data = await res.json();
  if (!Array.isArray(data.items)) {
    throw new Error("提醒的格式不正確");
  }
  return data;
}
