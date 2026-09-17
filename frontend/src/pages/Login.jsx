import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useSession } from "../context/SessionContext";
import ThemeToggle from "../components/ThemeToggle";

export default function Login() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const { login } = useSession();
  const navigate = useNavigate();

  async function handleSubmit(e) {
    e.preventDefault();
    setError("");
    setIsSubmitting(true);
    const result = await login(username, password);
    setIsSubmitting(false);

    if (result.ok) {
      navigate("/chat");
    } else {
      setError(result.message || "登入失敗，請再試一次。");
    }
  }

  function handleSkip() {
    login("", "");
    navigate("/chat");
  }

  return (
    <div className="login-page">
      <ThemeToggle />
      <div className="login-box">
        <h1>🎓 NCUXplore</h1>
        <p className="subtitle">中央大學校園智慧助手</p>

        <form onSubmit={handleSubmit}>
          <div className="login-field">
            <label htmlFor="username">Portal 帳號</label>
            <input
              id="username"
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              disabled={isSubmitting}
            />
          </div>
          <div className="login-field">
            <label htmlFor="password">Portal 密碼</label>
            <input
              id="password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              disabled={isSubmitting}
            />
          </div>

          <div className="login-trust-note">
            只有查詢「自己的」課表、時數進度、選課或報名活動時才需要帳密——用來自動幫你登入
            Portal／選課系統。密碼只會在登入當下送到我們自己的伺服器驗證一次，驗證完就不會
            再保留，瀏覽器裡也不會存密碼；之後每一輪對話只會用登入時換到的一次性通行證，不會
            再傳密碼。單純查法規、查活動列表不需要帳密。
          </div>

          {error && <div className="ncux-banner ncux-banner-warn login-error">⚠️ {error}</div>}

          <button type="submit" className="login-submit" disabled={isSubmitting}>
            {isSubmitting ? "登入中..." : "登入並開始使用"}
          </button>
        </form>

        <button type="button" className="login-skip" onClick={handleSkip} disabled={isSubmitting}>
          先不登入，只查法規／活動資訊
        </button>
      </div>
    </div>
  );
}
