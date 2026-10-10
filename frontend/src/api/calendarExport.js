/**
 * 呼叫 /api/calendar/export 下載行事曆檔（.ics）。沒登入只有校曆，有登入再加上這學期的課跟已報名的活動。
 * 跟推薦問題一樣用 POST 帶 token，不放在網址上。
 * 回傳實際包含的內容：["calendar", "classes", "activities"] 的子集合。
 */
export async function downloadCalendar(token) {
  const res = await fetch("/api/calendar/export", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token: token || "" }),
  });
  if (!res.ok) {
    let message = `匯出失敗（HTTP ${res.status}），請稍後再試。`;
    try {
      const data = await res.json();
      if (data.detail) message = data.detail;
    } catch {
      // 不是 JSON（例如後端沒開），用上面的訊息
    }
    throw new Error(message);
  }

  const contents = (res.headers.get("X-Calendar-Contents") || "").split(",").filter(Boolean);
  const personal = contents.includes("classes") || contents.includes("activities");
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = personal ? "我的中央大學行事曆.ics" : "中央大學校曆.ics";
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  return contents;
}

const CONTENT_NAMES = { calendar: "校曆", classes: "這學期的課", activities: "已報名的活動" };

/** ["calendar", "classes"] → 「校曆、這學期的課」 */
export function describeContents(contents) {
  return contents.map((c) => CONTENT_NAMES[c]).filter(Boolean).join("、");
}
