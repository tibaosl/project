import MessageContent from "./MessageContent";
import StatusIndicator from "./StatusIndicator";
import SourcesPanel from "./SourcesPanel";

export default function MessageList({ messages }) {
  return (
    <div>
      {messages.map((msg) => (
        <div className={`chat-message-row role-${msg.role}`} key={msg.id}>
          <div className="chat-bubble">
            {msg.role === "assistant" && msg.status && <StatusIndicator text={msg.status} />}
            {msg.content != null && <MessageContent content={msg.content} />}
            {msg.role === "assistant" && !msg.status && msg.content == null && !msg.isStreaming && (
              <span style={{ color: "var(--ncux-text-muted)" }}>（沒有取得回覆）</span>
            )}
            {msg.role === "assistant" && msg.sources && <SourcesPanel sources={msg.sources} />}
          </div>
        </div>
      ))}
    </div>
  );
}
