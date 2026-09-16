import { createContext, useContext, useState, useEffect, useLayoutEffect, useCallback, useMemo } from "react";

const ThemeContext = createContext(null);

const STORAGE_KEY = "ncuxplore_theme"; // "light" | "dark" | "system"
const VALID_MODES = ["light", "dark", "system"];

function loadStoredMode() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return VALID_MODES.includes(raw) ? raw : "system";
  } catch {
    return "system";
  }
}

function systemPrefersDark() {
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false;
}

export function ThemeProvider({ children }) {
  const [mode, setMode] = useState(() => loadStoredMode());
  const [resolved, setResolved] = useState(() => (mode === "system" ? (systemPrefersDark() ? "dark" : "light") : mode));

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, mode);
    } catch {
      // localStorage 存不進去（例如私密瀏覽模式）就算了，只是這次不會記住選擇。
    }

    if (mode !== "system") {
      setResolved(mode);
      return;
    }

    setResolved(systemPrefersDark() ? "dark" : "light");
    const mql = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = (e) => setResolved(e.matches ? "dark" : "light");
    mql.addEventListener("change", onChange);
    return () => mql.removeEventListener("change", onChange);
  }, [mode]);

  useLayoutEffect(() => {
    document.documentElement.dataset.theme = resolved;
  }, [resolved]);

  const setThemeMode = useCallback((next) => {
    if (VALID_MODES.includes(next)) {
      setMode(next);
    }
  }, []);

  const value = useMemo(() => ({ mode, resolved, setThemeMode }), [mode, resolved, setThemeMode]);

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme() {
  const ctx = useContext(ThemeContext);
  if (!ctx) {
    throw new Error("useTheme 必須在 <ThemeProvider> 底下使用");
  }
  return ctx;
}
