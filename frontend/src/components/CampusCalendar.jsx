import Icon from "./Icon";

// 事件類型的標籤（backend/analysis/academic_calendar.py 的 event_kind）
export const EVENT_KINDS = {
  holiday: ["放假", "ncux-badge-ok"],
  exam: ["考試", "ncux-badge-warn"],
  course: ["選課", "ncux-badge-info"],
  deadline: ["截止", "ncux-badge-warn"],
};

export function SourceLink({ source }) {
  if (!source) return null;
  const name = source.split("/").pop().replace(/\.[^.]+$/, "");
  return (
    <p className="ncux-card-meta ncux-meta-line">
      <Icon name="file" />
      <span>
        資料來源：
        <a href={`/files/${source.split("/").map(encodeURIComponent).join("/")}`} target="_blank" rel="noreferrer">
          {name}
        </a>
      </span>
    </p>
  );
}

/** 對應 kind === "campus_calendar"：校曆上符合的事件（get_campus_calendar）。 */
export default function CampusCalendar({ data }) {
  const events = data.events || [];
  return (
    <div>
      <div className="ncux-card-title ncux-with-icon">
        <Icon name="calendar" size={18} />
        <span>{data.title}</span>
      </div>
      {data.summary && <p className="calendar-summary">{data.summary}</p>}
      {events.length === 0 ? (
        <p className="ncux-card-meta">這段期間校曆上沒有特別的行事。</p>
      ) : (
        <ul className="calendar-list">
          {events.map((event, i) => {
            const kind = EVENT_KINDS[event.kind];
            return (
              <li key={`${event.start}-${i}`} className="calendar-item">
                <span className="calendar-date">{event.label}</span>
                <span className="calendar-title">{event.title}</span>
                <span className="calendar-tags">
                  {kind && <span className={`ncux-badge ${kind[1]}`}>{kind[0]}</span>}
                  {event.past && <span className="ncux-badge calendar-past">已經過了</span>}
                </span>
              </li>
            );
          })}
        </ul>
      )}
      <SourceLink source={data.source} />
    </div>
  );
}
