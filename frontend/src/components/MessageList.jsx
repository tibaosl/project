import { useRef } from "react";
import MessageContent from "./MessageContent";
import StatusIndicator from "./StatusIndicator";
import SourcesPanel from "./SourcesPanel";
import FollowUpSuggestions from "./FollowUpSuggestions";
import CopyAnswerButton from "./CopyAnswerButton";
import CachedAnswerNote from "./CachedAnswerNote";
import { AssistantAvatar, UserAvatar } from "./Avatar";

function MessageBubble({ msg, onAsk, onRefresh, busy }) {
  const contentRef = useRef(null);
  const isAssistant = msg.role === "assistant";
  const finished = !msg.isStreaming && !msg.status;
  // 文字回答（法規問答這類）講完才給複製。卡片本身就是整理好的畫面，反問使用者的句子也不用複製
  const copyable =
    isAssistant && typeof msg.content === "string" && msg.content.trim() && finished &&
    msg.suggestionKind !== "answers";
  // 法規問答用了之前整理好的回答（cached 事件），講完後註明是哪天整理的，給「重新查詢」
  const cached = isAssistant && msg.cached && finished;

  return (
    <div className="chat-bubble">
      {isAssistant && msg.status && <StatusIndicator text={msg.status} />}
      {msg.content != null && (
        <div ref={contentRef}>
          <MessageContent content={msg.content} onAsk={onAsk} />
        </div>
      )}
      {isAssistant && !msg.status && msg.content == null && !msg.isStreaming && (
        <span style={{ color: "var(--ncux-text-muted)" }}>（沒有取得回覆）</span>
      )}
      {isAssistant && msg.sources && <SourcesPanel sources={msg.sources} />}
      {(copyable || cached) && (
        <div className="answer-actions">
          {copyable && (
            <CopyAnswerButton
              // innerText 會保留段落換行（測試環境的 jsdom 沒有 innerText，退回 textContent）
              getText={() => contentRef.current?.innerText ?? contentRef.current?.textContent ?? msg.content}
              sources={msg.sources}
            />
          )}
          {cached && (
            <CachedAnswerNote
              created={msg.cached.created}
              standard={msg.cached.standard}
              onRefresh={onRefresh ? () => onRefresh(msg.id, msg.cached.key) : undefined}
              disabled={busy}
            />
          )}
        </div>
      )}
    </div>
  );
}

/** onRefresh(messageId, key)：快取的法規回答按了「重新查詢」，沒給就不顯示按鈕。 */
export default function MessageList({ messages, onAsk, onRefresh, busy = false }) {
  const last = messages[messages.length - 1];
  // 只在最新一則回覆下面給追問，舊的回覆不要一直掛著一排按鈕
  const showFollowUps = !busy && onAsk && last?.role === "assistant" && last.suggestions?.length > 0;

  return (
    <div>
      {messages.map((msg) => (
        <div className={`chat-message-row role-${msg.role}`} key={msg.id}>
          {msg.role === "assistant" && <AssistantAvatar />}
          <MessageBubble msg={msg} onAsk={onAsk} onRefresh={onRefresh} busy={busy} />
          {msg.role === "user" && <UserAvatar />}
        </div>
      ))}
      {showFollowUps && (
        <FollowUpSuggestions questions={last.suggestions} kind={last.suggestionKind} onAsk={onAsk} />
      )}
    </div>
  );
}
