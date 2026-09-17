import { useEffect, useRef, useState } from "react";
import { Navigate } from "react-router-dom";
import { useSession } from "../context/SessionContext";
import { streamChat, HttpStatusError } from "../api/chatStream";
import MessageList from "../components/MessageList";
import ChatInput from "../components/ChatInput";
import ThemeToggle from "../components/ThemeToggle";
import ConnectionBanner from "../components/ConnectionBanner";
import UnlockActionsBar from "../components/UnlockActionsBar";
import { useBackendStatus } from "../hooks/useBackendStatus";

function makeId() {
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export default function Chat() {
  const { session, isLoggedIn, logout, newConversation } = useSession();
  const [messages, setMessages] = useState([
    {
      id: makeId(),
      role: "assistant",
      content: "你好！我是 NCUXplore，今天想查點什麼？",
    },
  ]);
  const [isStreaming, setIsStreaming] = useState(false);
  const bodyRef = useRef(null);
  const { online, markUnreachable } = useBackendStatus();

  useEffect(() => {
    if (bodyRef.current) {
      bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
    }
  }, [messages]);

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
    let gotResult = false;

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
            gotResult = true;
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

    void gotResult; // 目前不需要用到，保留給之後想針對「有沒有結構化結果」加行為時用。
  }

  return (
    <div className="chat-page">
      <header className="chat-header">
        <h1>🎓 NCUXplore</h1>
        <div className="header-actions">
          {session.username && <span>已登入：{session.chineseName || session.username}</span>}
          <ThemeToggle />
          <button onClick={newConversation}>開新對話</button>
          <button onClick={logout}>登出</button>
        </div>
      </header>

      {session.username && !session.hasActionAccess && <UnlockActionsBar />}

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
