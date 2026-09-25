import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { useSession } from "../context/SessionContext";
import ThemeToggle from "../components/ThemeToggle";
import Logo from "../components/Logo";

export default function Login() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [showManualForm, setShowManualForm] = useState(false);
  const [isWaitingChrome, setIsWaitingChrome] = useState(false);
  const { login, loginWithChrome } = useSession();
  const navigate = useNavigate();
  const busy = isSubmitting || isWaitingChrome;

  // 對話中途登入失效時，Chat 頁會帶 login_error 導回這裡說明原因。
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const loginError = params.get("login_error");
    if (loginError) {
      setError(loginError);
      window.history.replaceState({}, "", "/login");
    }
  }, []);

  async function handleChromeLogin() {
    setError("");
    setIsWaitingChrome(true);
    const result = await loginWithChrome();
    setIsWaitingChrome(false);

    if (result.ok) {
      navigate("/chat");
    } else {
      setError(result.message || "登入失敗，請再試一次。");
    }
  }

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
        <h1><Logo size={35} /> NCUXplore</h1>
        <p className="subtitle">中央大學校園智慧助手</p>
        <div className="login-feature-pills">
          <span className="ncux-badge ncux-badge-info">法規查詢</span>
          <span className="ncux-badge ncux-badge-info">課表</span>
          <span className="ncux-badge ncux-badge-info">學業分析</span>
          <span className="ncux-badge ncux-badge-info">時數進度</span>
          <span className="ncux-badge ncux-badge-info">活動報名</span>
        </div>

        {error && <div className="ncux-banner ncux-banner-warn login-error">⚠️ {error}</div>}

        <button type="button" className="login-submit" onClick={handleChromeLogin} disabled={busy}>
          {isWaitingChrome ? "等待你在 Chrome 視窗登入..." : "使用中央大學 Portal 帳號登入"}
        </button>
        <p className="login-oauth-note">
          {isWaitingChrome
            ? "請到 Chrome 視窗登入 Portal（有驗證就勾選「我不是機器人」），登入完成後會自動回到這裡。「記住我」會先幫你勾好，29 天內不用再輸入密碼。"
            : "會開一個 Chrome 視窗讓你在 Portal 官方頁面登入，我們完全不會看到你的密碼；登入一次就能使用課表、時數、學業分析等功能。"}
        </p>

        {!showManualForm ? (
          <button
            type="button"
            className="login-skip"
            onClick={() => setShowManualForm(true)}
            disabled={busy}
          >
            改用帳號密碼手動登入
          </button>
        ) : (
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
                required
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
                required
              />
            </div>

            <div className="login-trust-note">
              只有查詢「自己的」課表、時數進度、選課或報名活動時才需要帳密——用來自動幫你登入
              Portal／選課系統。密碼只會在登入當下送到我們自己的伺服器驗證一次，驗證完就不會
              再保留，瀏覽器裡也不會存密碼；之後每一輪對話只會用登入時換到的一次性通行證，不會
              再傳密碼。單純查法規、查活動列表不需要帳密。
            </div>

            <button type="submit" className="login-submit" disabled={isSubmitting}>
              {isSubmitting ? "登入中..." : "登入並開始使用"}
            </button>
          </form>
        )}

        <button type="button" className="login-skip" onClick={handleSkip} disabled={busy}>
          先不登入，只查法規／活動資訊
        </button>
      </div>
    </div>
  );
}
