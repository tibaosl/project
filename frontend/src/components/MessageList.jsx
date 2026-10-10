import { useRef } from "react";
import MessageContent from "./MessageContent";
import StatusIndicator from "./StatusIndicator";
import SourcesPanel from "./SourcesPanel";
import FollowUpSuggestions from "./FollowUpSuggestions";
import CopyAnswerButton from "./CopyAnswerButton";
import { AssistantAvatar, UserAvatar } from "./Avatar";

function MessageBubble({ msg }) {
  const contentRef = useRef(null);
  const isAssistant = msg.role === "assistant";
  // 文字回答（法規問答這類）講完才給複製。卡片本身就是整理好的畫面，反問使用者的句子也不用複製
  const copyable =
    isAssistant && typeof msg.content === "string" && msg.content.trim() && !msg.isStreaming && !msg.status &&
    msg.suggestionKind !== "answers";

  return (
    <div className="chat-bubble">
      {isAssistant && msg.status && <StatusIndicator text={msg.status} />}
      {msg.content != null && (
        <div ref={contentRef}>
          <MessageContent content={msg.content} />
        </div>
      )}
      {isAssistant && !msg.status && msg.content == null && !msg.isStreaming && (
        <span style={{ color: "var(--ncux-text-muted)" }}>（沒有取得回覆）</span>
      )}
      {isAssistant && msg.sources && <SourcesPanel sources={msg.sources} />}
      {copyable && (
        <CopyAnswerButton
          // innerText 會保留段落換行（測試環境的 jsdom 沒有 innerText，退回 textContent）
          getText={() => contentRef.current?.innerText ?? contentRef.current?.textContent ?? msg.content}
          sources={msg.sources}
        />
      )}
    </div>
  );
}

export default function MessageList({ messages, onAsk, busy = false }) {
  const last = messages[messages.length - 1];
  // 只在最新一則回覆下面給追問，舊的回覆不要一直掛著一排按鈕
  const showFollowUps = !busy && onAsk && last?.role === "assistant" && last.suggestions?.length > 0;

  return (
    <div>
      {messages.map((msg) => (
        <div className={`chat-message-row role-${msg.role}`} key={msg.id}>
          {msg.role === "assistant" && <AssistantAvatar />}
          <MessageBubble msg={msg} />
          {msg.role === "user" && <UserAvatar />}
        </div>
      ))}
      {showFollowUps && (
        <FollowUpSuggestions questions={last.suggestions} kind={last.suggestionKind} onAsk={onAsk} />
      )}
    </div>
  );
}
