/**
 * 回答結束後的選項，點一下就直接送出：
 * - kind="answers"：系統剛剛在反問使用者，選項是那個反問的回答（例如學院名稱、要／不用了）
 * - 其他：「你可能還想問」的追問
 */
export default function FollowUpSuggestions({ questions, onAsk, kind = "follow_ups" }) {
  return (
    <div className="follow-ups">
      <div className="follow-ups-title">{kind === "answers" ? "直接點選回答" : "你可能還想問"}</div>
      <div className="follow-ups-list">
        {questions.map((q) => (
          <button type="button" key={q} className="follow-up-chip" onClick={() => onAsk(q)}>
            {q}
          </button>
        ))}
      </div>
    </div>
  );
}
