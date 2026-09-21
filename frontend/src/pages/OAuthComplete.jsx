import { useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { useSession } from "../context/SessionContext";
import Logo from "../components/Logo";

// 後端 /api/oauth/callback 完成 Portal OAuth 授權流程後，會把瀏覽器導來這頁，
// URL 帶著 token/username（見 oauth_portal.build_return_url）。這裡只做一件
// 事：把這些值存進 session，然後馬上把它們從網址列洗掉（用 navigate replace
// 到 /chat，不留在瀏覽器歷史紀錄裡，避免 token 留在網址列/上一頁按鈕可以
// 回得去的地方）。
export default function OAuthComplete() {
  const { loginWithToken } = useSession();
  const navigate = useNavigate();
  const handled = useRef(false);

  useEffect(() => {
    if (handled.current) return;
    handled.current = true;

    const params = new URLSearchParams(window.location.search);
    const token = params.get("token");
    const username = params.get("username");
    const chineseName = params.get("chinese_name") || "";

    if (token && username) {
      loginWithToken(username, token, chineseName);
      navigate("/chat", { replace: true });
    } else {
      navigate("/login?login_error=登入流程未完成，請再試一次。", { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="login-page">
      <div className="login-box">
        <h1><Logo size={35} /> NCUXplore</h1>
        <p className="subtitle">登入處理中，請稍候...</p>
      </div>
    </div>
  );
}
