import Icon, { MetaLine } from "./Icon";

const MODE_LABEL = {
  online: ["線上報名", "ncux-badge-ok"],
  onsite: ["現場報名", "ncux-badge-warn"],
};

/** 單一場次的資訊卡片，活動詳情、報名確認都共用這個。 */
export function ActivitySessionCard({ session }) {
  const [modeText, modeClass] = MODE_LABEL[session.registration_mode] || ["報名方式不明", "ncux-badge-info"];

  const hourTags = [
    ["學習護照時數", session.passport_hours_tag],
    ["軟實力時數", session.soft_skill_hours_tag],
  ].filter(([, tag]) => tag && !tag.includes("不提供時數"));

  return (
    <div className="ncux-card">
      <div className="ncux-card-title">
        {session.session_name || "（未命名場次）"}{" "}
        <span className={`ncux-badge ${modeClass}`}>
          {session.registration_mode === "onsite" && (
            <>
              <Icon name="alert" />{" "}
            </>
          )}
          {modeText}
        </span>
      </div>
      {session.instructor && <MetaLine icon="user">{session.instructor}</MetaLine>}
      {session.location && <MetaLine icon="pin">地點：{session.location}</MetaLine>}
      {session.event_period && <MetaLine icon="clock">活動時間：{session.event_period}</MetaLine>}
      {session.signup_period && <MetaLine icon="form">報名時間：{session.signup_period}</MetaLine>}
      {hourTags.map(([label, tag]) => (
        <MetaLine icon="medal" key={label}>
          {label}：{tag}
        </MetaLine>
      ))}
      {session.signup_status_text && <MetaLine icon="users">{session.signup_status_text}</MetaLine>}
    </div>
  );
}

/** 依標籤查詢/推薦結果的單筆活動卡片（比場次卡片再多一個「推薦原因」）。 */
export function ActivityMatchCard({ item }) {
  const headcount =
    item.signup_status_text || (item.capacity != null ? `名額上限：${item.capacity}` : null);

  return (
    <div className="ncux-card">
      <div className="ncux-card-title">{item.activity_title}</div>
      <div className="ncux-card-meta">場次：{item.session_name}</div>
      {item.event_period && <MetaLine icon="clock">活動時間：{item.event_period}</MetaLine>}
      {item.signup_period && <MetaLine icon="form">報名時間：{item.signup_period}</MetaLine>}
      {item.tag && <MetaLine icon="medal">時數標籤：{item.tag}</MetaLine>}
      {headcount && <MetaLine icon="users">{headcount}</MetaLine>}
      {item.reason && (
        <MetaLine icon="bulb" className="ncux-reason">
          {item.reason}
        </MetaLine>
      )}
    </div>
  );
}
