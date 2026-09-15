import { createContext, useContext, useState, useCallback } from "react";

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

  const login = useCallback((username, password) => {
    const next = { username, password, threadId: makeThreadId() };
    setSession(next);
    persistSession(next);
  }, []);

  const logout = useCallback(() => {
    setSession(null);
    persistSession(null);
  }, []);

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
