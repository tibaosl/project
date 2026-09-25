import MessageContent from "./MessageContent";
import StatusIndicator from "./StatusIndicator";
import SourcesPanel from "./SourcesPanel";
import FollowUpSuggestions from "./FollowUpSuggestions";
import { AssistantAvatar, UserAvatar } from "./Avatar";

export default function MessageList({ messages, onAsk, busy = false }) {
  const last = messages[messages.length - 1];
  // 只在最新一則回覆下面給追問，舊的回覆不要一直掛著一排按鈕
  const showFollowUps = !busy && onAsk && last?.role === "assistant" && last.suggestions?.length > 0;

  return (
    <div>
      {messages.map((msg) => (
        <div className={`chat-message-row role-${msg.role}`} key={msg.id}>
          {msg.role === "assistant" && <AssistantAvatar />}
          <div className="chat-bubble">
            {msg.role === "assistant" && msg.status && <StatusIndicator text={msg.status} />}
            {msg.content != null && <MessageContent content={msg.content} />}
            {msg.role === "assistant" && !msg.status && msg.content == null && !msg.isStreaming && (
              <span style={{ color: "var(--ncux-text-muted)" }}>（沒有取得回覆）</span>
            )}
            {msg.role === "assistant" && msg.sources && <SourcesPanel sources={msg.sources} />}
          </div>
          {msg.role === "user" && <UserAvatar />}
        </div>
      ))}
      {showFollowUps && <FollowUpSuggestions questions={last.suggestions} onAsk={onAsk} />}
    </div>
  );
}
