import { useState } from "react";
import { useSession } from "../context/SessionContext";

// 用 Portal OAuth 登入只驗證了身分（見 pages/Login.jsx 的「使用 Portal 帳號
// 登入」），還沒辦法用課表/時數/選課這些需要自動化操作 Portal/選課系統的
// 功能——中大官方 OAuth API 沒有提供這些資料/操作的介面，只能透過
// Playwright 背景瀏覽器，而那個仍然需要密碼才能登入一次。這裡讓使用者
// 「補一次」密碼來啟用這些功能，跟一開始的身分登入是分開的兩件事。
//
// 「已啟用」直接看 session.hasActionAccess，不要另外開一個本地 state
// 重複記同一件事——父層 Chat.jsx 負責控制這個元件在解鎖成功後要繼續
// 顯示多久（讓使用者看得到這個「已啟用」訊息），這個元件本身只管
// 「現在該顯示什麼」，不管「還要不要留在畫面上」。
export default function UnlockActionsBar() {
  const [expanded, setExpanded] = useState(false);
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const { session, unlockActionsWithPassword } = useSession();

  if (session?.hasActionAccess) {
    return <div className="unlock-actions-bar unlock-actions-done">✅ 已啟用課表/時數/報名功能</div>;
  }

  if (!expanded) {
    return (
      <button type="button" className="unlock-actions-toggle" onClick={() => setExpanded(true)}>
        🔓 設定 Portal 密碼以啟用課表/時數/報名功能
      </button>
    );
  }

  async function handleSubmit(e) {
    e.preventDefault();
    setError("");
    setIsSubmitting(true);
    const result = await unlockActionsWithPassword(password);
    setIsSubmitting(false);

    if (result.ok) {
      setPassword("");
    } else {
      setError(result.message || "設定失敗，請再試一次。");
    }
  }

  return (
    <form className="unlock-actions-bar" onSubmit={handleSubmit}>
      <span>Portal 密碼</span>
      <input
        type="password"
        value={password}
        onChange={(e) => setPassword(e.target.value)}
        disabled={isSubmitting}
        autoFocus
      />
      <button type="submit" disabled={isSubmitting || !password}>
        {isSubmitting ? "設定中..." : "確認"}
      </button>
      <button type="button" onClick={() => setExpanded(false)} disabled={isSubmitting}>
        取消
      </button>
      {error && <span className="unlock-actions-error">⚠️ {error}</span>}
    </form>
  );
}
