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
 *   {type: "result", content}
 *   {type: "sources", sources}
 *   {type: "error", message}
 *   {type: "done"}
 */
export async function streamChat({ userMessage, username, password, threadId }, onEvent, { signal } = {}) {
  const response = await fetch("/api/chat/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      user_message: userMessage,
      username: username || "",
      password: password || "",
      thread_id: threadId,
    }),
    signal,
  });

  if (!response.ok || !response.body) {
    throw new Error(`HTTP ${response.status}`);
  }

  const reader = response.body.getReader();
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
