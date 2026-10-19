/**
 * 呼叫 /api/chat/stream，逐行解析 SSE 事件，透過 onEvent callback 往外送。
 *
 * 不用瀏覽器內建的 EventSource（那個只能發 GET、不能帶 JSON body），
 * 改用 fetch + ReadableStream 自己解析 `data: {...}\n\n` 格式——跟後端
 * main.py 的 StreamingResponse 是同一套協定。
 *
 * 事件格式（跟 supervisor_agent.run_ncuxplore_agent_stream() 一致）：
 *   {type: "status", text}
 *   {type: "token", text}
 *   {type: "cached", key, created}      # 法規問答直接用了之前整理好的回答（後端的回答快取），
 *                                       # key 給「重新查詢」用（streamAnswerRefresh）
 *   {type: "result", content}
 *   {type: "sources", sources}
 *   {type: "error", message}
 *   {type: "session_expired", message}
 *   {type: "done"}                      # 回答結束，可以解鎖輸入框
 *   {type: "suggestions", questions, kind}  # done 之後才來（可能沒有）：kind 是 "answers" 時是
 *                                           # 系統反問的回答選項，"follow_ups" 是你可能還想問
 */

// 跟單純的網路連線失敗（fetch 直接 throw TypeError）分開，讓呼叫端可以
// 判斷「伺服器有回應，只是這次請求本身失敗」還是「根本連不上伺服器」。
export class HttpStatusError extends Error {
  constructor(status) {
    super(`HTTP ${status}`);
    this.status = status;
  }
}

export async function streamChat({ userMessage, username, token, threadId }, onEvent, { signal } = {}) {
  const response = await fetch("/api/chat/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      user_message: userMessage,
      username: username || "",
      token: token || "",
      thread_id: threadId,
    }),
    signal,
  });

  if (!response.ok || !response.body) {
    throw new HttpStatusError(response.status);
  }

  await readEventStream(response.body, onEvent);
}

/**
 * 法規問答的「重新查詢」（/api/regulations/refresh）：畫面上那則回答是之前整理好的（cached 事件），
 * 後端不用快取重新挑文件、回答。事件只有 status、token、sources、error、done，格式跟 streamChat 一樣。
 * 找不到原本的問題時後端回 404（HttpStatusError）。
 */
export async function streamAnswerRefresh({ key }, onEvent, { signal } = {}) {
  const response = await fetch("/api/regulations/refresh", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ key }),
    signal,
  });

  if (!response.ok || !response.body) {
    throw new HttpStatusError(response.status);
  }

  await readEventStream(response.body, onEvent);
}

/**
 * 逐一解析 `data: {...}

` 格式的事件交給 onEvent，串流結束才回來。
 * /api/chat/stream 跟 /api/login/chrome 共用。
 */
export async function readEventStream(body, onEvent) {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const chunks = buffer.split("\n\n");
    buffer = chunks.pop() ?? "";

    for (const chunk of chunks) {
      const line = chunk.trim();
      if (!line.startsWith("data:")) continue;
      const jsonText = line.slice(5).trim();
      if (!jsonText) continue;
      try {
        onEvent(JSON.parse(jsonText));
      } catch (err) {
        console.error("解析 SSE 事件失敗：", err, jsonText);
      }
    }
  }
}
