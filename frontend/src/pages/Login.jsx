import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useSession } from "../context/SessionContext";

export default function Login() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const { login } = useSession();
  const navigate = useNavigate();

  function handleSubmit(e) {
    e.preventDefault();
    login(username, password);
    navigate("/chat");
  }

  function handleSkip() {
    login("", "");
    navigate("/chat");
  }

  return (
    <div className="login-page">
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
            />
          </div>

          <div className="login-trust-note">
            只有查詢「自己的」課表、時數進度、選課或報名活動時才需要帳密——用來自動幫你登入
            Portal／選課系統，密碼只會暫存在這個瀏覽器分頁，關閉分頁就會清除，不會傳到除了學校系統以外的地方。
            單純查法規、查活動列表不需要帳密。
          </div>

          <button type="submit" className="login-submit">
            登入並開始使用
          </button>
        </form>

        <button type="button" className="login-skip" onClick={handleSkip}>
          先不登入，只查法規／活動資訊
        </button>
      </div>
    </div>
  );
}
