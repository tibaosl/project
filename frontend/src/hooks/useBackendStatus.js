import { useEffect, useRef, useState } from "react";

const POLL_INTERVAL_MS = 15000;
const FETCH_TIMEOUT_MS = 5000;

async function pingHealth() {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
  try {
    const res = await fetch("/api/health", { signal: controller.signal });
    return res.ok;
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

// 定期 ping /api/health，讓畫面上可以顯示「後端連不上」的提示，而不是
// 每次傳訊息失敗才各自顯示一次錯誤泡泡。`markUnreachable()` 讓呼叫端
// 在自己那次請求就失敗時，能立刻反映在畫面上，不用等下一次輪詢。
export function useBackendStatus() {
  const [online, setOnline] = useState(true);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;

    async function poll() {
      const ok = await pingHealth();
      if (mountedRef.current) setOnline(ok);
    }

    poll();
    const id = setInterval(poll, POLL_INTERVAL_MS);

    return () => {
      mountedRef.current = false;
      clearInterval(id);
    };
  }, []);

  function markUnreachable() {
    setOnline(false);
  }

  return { online, markUnreachable };
}
