import { useEffect, useRef, useState } from "react";
import Icon from "./Icon";

/** 「參考資料」附在複製的文字最後，只放文件名稱（/files 的連結離開這台電腦就打不開）。 */
export function answerWithSources(text, sources) {
  const names = (sources || []).map((src) => src.split(" (")[0].split("/").pop().replace(/\.[^.]+$/, ""));
  const unique = [...new Set(names)].filter(Boolean);
  return unique.length ? `${text}\n\n參考資料：${unique.join("、")}` : text;
}

/**
 * 文字回答下面的複製按鈕。getText 回傳畫面上呈現的文字（不含 Markdown 的 ** 這類符號），
 * 學生常把法規回答貼給同學或存起來。
 */
export default function CopyAnswerButton({ getText, sources }) {
  const [state, setState] = useState("idle");
  const timer = useRef(null);
  useEffect(() => () => clearTimeout(timer.current), []);

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(answerWithSources(getText().trim(), sources));
      setState("copied");
    } catch {
      setState("failed");
    }
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setState("idle"), 2000);
  }

  const label = state === "copied" ? "已複製" : state === "failed" ? "無法複製" : "複製";
  return (
    <button type="button" className="copy-answer-button" onClick={handleCopy} aria-label="複製這則回答">
      <Icon name={state === "copied" ? "check" : "copy"} />
      <span aria-live="polite">{label}</span>
    </button>
  );
}
