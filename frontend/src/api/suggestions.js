/**
 * 呼叫 /api/suggestions 取得開場推薦問題（後端每次隨機挑，沒登入只給不需登入的功能）。
 * exclude 是畫面上正在顯示的題目（「換一批」時盡量不要再出現）。
 * 回傳 [{ question, label }]。
 */
export async function fetchStarterQuestions(token, exclude = []) {
  const res = await fetch("/api/suggestions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token: token || "", exclude }),
  });
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`);
  }
  const data = await res.json();
  if (!Array.isArray(data.questions)) {
    throw new Error("推薦問題格式不正確");
  }
  return data.questions;
}
