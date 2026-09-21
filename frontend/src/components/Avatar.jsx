// 聊天訊息的頭像圖示。原本用 🤖（機器人）/ 🧑（使用者）emoji，使用者反映
// 🧑 這個 emoji 在部分字型下比較像嬰兒、不好看。改成跟 Logo.jsx 同一套風格
// 的線條 SVG：助理是一個「AI 亮點」四角星（許多助理類產品的通用符號，
// 例如 Gemini/Copilot），使用者是一個簡單的人形剪影，都用 currentColor
// 上色，跟著 .chat-avatar 的背景色系走，不會有 emoji 的跨平台渲染落差。
export function AssistantAvatar() {
  return (
    <div className="chat-avatar chat-avatar-assistant" aria-hidden="true">
      <svg viewBox="0 0 24 24" width="15" height="15">
        <path
          d="M12 3c.45 2.9 1.25 4.9 2.35 6S17 10.6 20 11c-3 .4-5 1.2-6.1 2.3S12.4 16 12 19c-.4-3-1.2-5-2.3-6.1S6.9 11.4 4 11c2.9-.4 4.9-1.2 6-2.3S11.6 5.9 12 3Z"
          fill="currentColor"
        />
        <path
          d="M18.5 3c.25 1.15.65 1.95 1.15 2.45S20.85 6.3 22 6.5c-1.15.2-1.95.6-2.45 1.1S18.7 8.85 18.5 10c-.2-1.15-.6-1.95-1.1-2.45S16.15 6.7 15 6.5c1.15-.2 1.95-.6 2.45-1.1S18.3 4.15 18.5 3Z"
          fill="currentColor"
        />
      </svg>
    </div>
  );
}

export function UserAvatar() {
  return (
    <div className="chat-avatar chat-avatar-user" aria-hidden="true">
      <svg viewBox="0 0 24 24" width="15" height="15">
        <circle cx="12" cy="8.3" r="3.6" fill="currentColor" />
        <path d="M4.5 20a7.5 7.5 0 0 1 15 0Z" fill="currentColor" />
      </svg>
    </div>
  );
}
