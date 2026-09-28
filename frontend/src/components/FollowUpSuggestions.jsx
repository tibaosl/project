/** 回答結束後的「你可能還想問」，點一下就直接送出那個問題。 */
export default function FollowUpSuggestions({ questions, onAsk }) {
  return (
    <div className="follow-ups">
      <div className="follow-ups-title">你可能還想問</div>
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
