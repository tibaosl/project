import { useCallback, useEffect, useState } from "react";
import { fetchStarterQuestions } from "../api/suggestions";
import Icon from "./Icon";
import Logo from "./Logo";

const SKELETON_COUNT = 6;

/** 對話還沒開始時的歡迎畫面：打招呼 + 一組可以直接點的推薦問題。 */
export default function WelcomePanel({ chineseName, isGuest, token, onAsk }) {
  const [questions, setQuestions] = useState([]);
  const [status, setStatus] = useState("loading");

  const load = useCallback(async (exclude = []) => {
    setStatus("loading");
    try {
      setQuestions(await fetchStarterQuestions(token, exclude));
      setStatus("ready");
    } catch {
      setStatus("error");
    }
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <section className="welcome" aria-label="開始對話">
      <div className="welcome-hero">
        <div className="welcome-logo">
          <Logo size={40} />
        </div>
        <h2>{chineseName ? `嗨，${chineseName}！` : "嗨，歡迎使用 NCUXplore！"}</h2>
        <p>直接用中文問我校園大小事，或是點一個問題開始：</p>
      </div>

      {status === "error" ? (
        <p className="welcome-error">暫時拿不到推薦問題，直接在下面輸入也可以喔。</p>
      ) : (
        <div className="suggestion-grid" aria-busy={status === "loading"}>
          {status === "loading"
            ? Array.from({ length: SKELETON_COUNT }, (_, i) => (
                <div key={i} className="suggestion-card suggestion-card-skeleton" aria-hidden="true" />
              ))
            : questions.map((q) => (
                <button
                  type="button"
                  key={q.question}
                  className="suggestion-card"
                  onClick={() => onAsk(q.question)}
                >
                  <span className="suggestion-card-label">{q.label}</span>
                  <span className="suggestion-card-text">{q.question}</span>
                  <span className="suggestion-card-arrow" aria-hidden="true">
                    <Icon name="arrow-right" size={16} />
                  </span>
                </button>
              ))}
        </div>
      )}

      <div className="welcome-footer">
        <button
          type="button"
          className="suggestion-refresh"
          onClick={() => load(questions.map((q) => q.question))}
          disabled={status === "loading"}
        >
          <Icon name="refresh" /> 換一批
        </button>
        {isGuest && <span>登入後還能查詢自己的課表、學分、時數等個人資料</span>}
      </div>
    </section>
  );
}
