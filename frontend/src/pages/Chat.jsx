import { useEffect, useRef, useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { useSession } from "../context/SessionContext";
import { streamChat, HttpStatusError } from "../api/chatStream";
import MessageList from "../components/MessageList";
import ChatInput from "../components/ChatInput";
import ThemeToggle from "../components/ThemeToggle";
import ConnectionBanner from "../components/ConnectionBanner";
import { useBackendStatus } from "../hooks/useBackendStatus";
import Logo from "../components/Logo";

function makeId() {
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function makeGreeting() {
  return [
    {
      id: makeId(),
      role: "assistant",
      content: "你好！我是 NCUXplore，今天想查點什麼？",
    },
  ];
}

export default function Chat() {
  const { session, isLoggedIn, logout, newConversation } = useSession();
  const navigate = useNavigate();
  const [messages, setMessages] = useState(() => makeGreeting());
  const [isStreaming, setIsStreaming] = useState(false);
  const bodyRef = useRef(null);
  const { online, markUnreachable } = useBackendStatus();

  useEffect(() => {
    if (bodyRef.current) {
      bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
    }
  }, [messages]);

  // ⚠️ hooks 都在下面「沒登入就導回登入頁」的 guard 之前，要用 `session?.`——
  // 登出當下 session 先變成 null，Chat 還會再 render 一次，直接寫
  // `session.xxx` 會在導頁之前丟例外、整頁卡在空白畫面。
  // 「開新對話」只是換掉 threadId（見 SessionContext.newConversation），
  // 本身不會動到畫面上的訊息列表——沒有這個 effect 的話，使用者點了會
  // 覺得「按了跟沒按一樣」，因為聊天記錄完全沒變。開新的 threadId 就清空
  // 畫面回到初始問候語，讓使用者看得到真的換了一個新對話。
  useEffect(() => {
    setMessages(makeGreeting());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session?.threadId]);

  if (!isLoggedIn) {
    return <Navigate to="/login" replace />;
  }

  function updateMessage(id, updater) {
    setMessages((prev) => prev.map((m) => (m.id === id ? updater(m) : m)));
  }

  async function handleSend(text) {
    const userMsg = { id: makeId(), role: "user", content: text };
    const assistantId = makeId();
    const assistantMsg = { id: assistantId, role: "assistant", content: null, status: null, isStreaming: true };
    setMessages((prev) => [...prev, userMsg, assistantMsg]);
    setIsStreaming(true);

    let tokenBuffer = "";

    try {
      await streamChat(
        { userMessage: text, username: session.username, token: session.token, threadId: session.threadId },
        (event) => {
          if (event.type === "status") {
            updateMessage(assistantId, (m) => ({ ...m, status: event.text }));
          } else if (event.type === "token") {
            tokenBuffer += event.text;
            const snapshot = tokenBuffer;
            updateMessage(assistantId, (m) => ({ ...m, status: null, content: snapshot }));
          } else if (event.type === "result") {
            updateMessage(assistantId, (m) => ({ ...m, status: null, content: event.content }));
          } else if (event.type === "sources") {
            updateMessage(assistantId, (m) => ({ ...m, sources: event.sources }));
          } else if (event.type === "error") {
            updateMessage(assistantId, (m) => ({
              ...m,
              status: null,
              content: (m.content ? `${m.content}\n\n` : "") + `⚠️ ${event.message}`,
            }));
          } else if (event.type === "done") {
            updateMessage(assistantId, (m) => ({ ...m, status: null, isStreaming: false }));
          } else if (event.type === "session_expired") {
            // 後端明確告訴我們 token 已經失效了（跟單純訪客模式不一樣，見
            // main.py _resolve_credentials 的說明）——清掉這個過期的本地
            // session，導回登入頁並說明原因，不要讓使用者繼續對著一個
            // 悄悄變成訪客模式的畫面納悶為什麼查不到自己的資料。
            logout();
            navigate(`/login?login_error=${encodeURIComponent(event.message)}`, { replace: true });
          }
        }
      );
    } catch (err) {
      if (!(err instanceof HttpStatusError)) {
        markUnreachable();
      }
      updateMessage(assistantId, (m) => ({
        ...m,
        status: null,
        isStreaming: false,
        content: m.content || `⚠️ 連線發生錯誤：${err.message}`,
      }));
    } finally {
      setIsStreaming(false);
    }
  }

  return (
    <div className="chat-page">
      <header className="chat-header">
        <h1><Logo size={27} /> NCUXplore</h1>
        <div className="header-actions">
          {session.username && <span>已登入：{session.chineseName || session.username}</span>}
          <ThemeToggle />
          <button onClick={newConversation}>開新對話</button>
          <button onClick={logout}>登出</button>
        </div>
      </header>

      <div className="chat-body" ref={bodyRef}>
        {!online && <ConnectionBanner />}
        <MessageList messages={messages} />
      </div>

      <div className="chat-input-bar">
        <ChatInput onSend={handleSend} disabled={isStreaming} />
      </div>
    </div>
  );
}
