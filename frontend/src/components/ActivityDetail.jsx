import { ActivitySessionCard } from "./ActivityCard";

/** 對應 kind === "activity_detail" / "activity_confirmation"。 */
export default function ActivityDetail({ data }) {
  const confirmationLabel = data.kind === "activity_confirmation" ? data.action_label : null;

  return (
    <div>
      <h3 style={{ fontSize: 16, margin: "4px 0 6px" }}>{data.title || "未知活動"}</h3>

      {(data.department || data.contact_person) && (
        <div className="ncux-card-meta" style={{ marginBottom: 8 }}>
          {data.department && `承辦單位：${data.department}`}
          {data.department && data.contact_person && " ｜ "}
          {data.contact_person &&
            `承辦人：${data.contact_person}${data.contact_email ? `（${data.contact_email}）` : ""}`}
        </div>
      )}

      {data.description && (
        <div className="ncux-card">
          <div className="ncux-card-title">活動內容</div>
          <div className="ncux-card-meta" style={{ whiteSpace: "pre-wrap" }}>
            {data.description}
          </div>
        </div>
      )}

      {(data.sessions || []).map((session, i) => (
        <ActivitySessionCard session={session} key={session.session_id || i} />
      ))}

      {confirmationLabel && (
        <div className="ncux-confirm-box">
          ⚠️ 確定要{confirmationLabel}這個活動嗎？這個動作會真的改變你在學校系統上的報名紀錄，
          請回覆「確定{confirmationLabel}」來送出。
        </div>
      )}
    </div>
  );
}
