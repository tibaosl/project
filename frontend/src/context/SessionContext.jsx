import { createContext, useContext, useState, useCallback } from "react";
import { requestLogin, requestLogout } from "../api/auth";

const SessionContext = createContext(null);

const STORAGE_KEY = "ncuxplore_session";

function loadStoredSession() {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    // 讀 sessionStorage 失敗（例如私密瀏覽模式擋掉）就當作沒登入，
    // 不要讓整個 App 崩掉。
    return null;
  }
}

function persistSession(session) {
  try {
    if (session) {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(session));
    } else {
      sessionStorage.removeItem(STORAGE_KEY);
    }
  } catch {
    // 存不進去就算了（例如私密瀏覽模式），不影響當下這次對話。
  }
}

function makeThreadId() {
  if (window.crypto?.randomUUID) {
    return window.crypto.randomUUID();
  }
  // 極舊瀏覽器的備援，不需要真的密碼學等級的隨機性，只要每個分頁
  // 不要撞到同一個 thread_id 就好。
  return `thread-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function SessionProvider({ children }) {
  const [session, setSession] = useState(() => loadStoredSession());

  // 登入成功後只存 token，不存密碼——sessionStorage 裡、之後每一輪對話
  // 送出的請求裡都不會再出現明文密碼（見 api/chatStream.js、main.py 的
  // /api/login、/api/chat(/stream)）。
  //
  // 回傳 { ok, message }，不是丟例外：帳密打錯是很常見、預期內的情況，
  // 讓呼叫端（Login 頁）用一般的 if/else 處理錯誤訊息，不用包 try/catch。
  const login = useCallback(async (username, password) => {
    if (!username || !password) {
      // 「先不登入」的訪客模式：不呼叫後端，直接開一個沒有 token 的 session。
      const next = { username: username || "", token: "", threadId: makeThreadId() };
      setSession(next);
      persistSession(next);
      return { ok: true };
    }

    try {
      const token = await requestLogin(username, password);
      const next = { username, token, threadId: makeThreadId() };
      setSession(next);
      persistSession(next);
      return { ok: true };
    } catch (err) {
      return { ok: false, message: err.message };
    }
  }, []);

  const logout = useCallback(() => {
    if (session?.token) {
      // 登出是收尾動作，不等後端回應、不擋 UI；requestLogout 內部本來就
      // 會吞掉失敗（例如剛好連不上後端）。
      void requestLogout(session.token, session.username);
    }
    setSession(null);
    persistSession(null);
  }, [session]);

  const newConversation = useCallback(() => {
    setSession((prev) => {
      if (!prev) return prev;
      const next = { ...prev, threadId: makeThreadId() };
      persistSession(next);
      return next;
    });
  }, []);

  const value = {
    session,
    isLoggedIn: !!session,
    login,
    logout,
    newConversation,
  };

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession() {
  const ctx = useContext(SessionContext);
  if (!ctx) {
    throw new Error("useSession 必須在 <SessionProvider> 底下使用");
  }
  return ctx;
}
