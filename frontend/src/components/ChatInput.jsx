import { useState } from "react";

export default function ChatInput({ onSend, disabled }) {
  const [value, setValue] = useState("");

  function submitIfValid() {
    const trimmed = value.trim();
    if (!trimmed || disabled) return;
    onSend(trimmed);
    setValue("");
  }

  function handleSubmit(e) {
    e.preventDefault();
    submitIfValid();
  }

  // 除了 <form onSubmit>，額外在 input 上直接接 Enter 鍵：單一文字輸入框
  // 按 Enter 理論上瀏覽器會自動觸發 form 的 submit 事件，但避免不同瀏覽器
  // /輸入法組合鍵情境下不觸發，這裡直接處理 Enter，行為更可預期。
  function handleKeyDown(e) {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submitIfValid();
    }
  }

  return (
    <form className="chat-input-form" onSubmit={handleSubmit}>
      <input
        type="text"
        placeholder="請輸入你的問題（例如：資工系英文畢業門檻）..."
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={handleKeyDown}
        disabled={disabled}
      />
      <button type="submit" disabled={disabled || !value.trim()}>
        送出
      </button>
    </form>
  );
}
