import Icon, { MetaLine } from "./Icon";
import { EVENT_KINDS } from "./CampusCalendar";

function startOf(time) {
  const match = /(\d{1,2}):(\d{2})/.exec(time || "");
  return match ? Number(match[1]) * 60 + Number(match[2]) : 24 * 60;
}

/** 一天的行程：放假原因、校曆事件放最上面，課跟活動依開始時間排在一起。 */
function AgendaDay({ day }) {
  const timed = [
    ...day.classes.map((c) => ({ ...c, type: "class" })),
    ...day.activities.map((a) => ({ ...a, type: "activity" })),
  ].sort((a, b) => startOf(a.time) - startOf(b.time));
  const empty = !day.holiday && timed.length === 0 && day.events.length === 0;

  return (
    <div className="ncux-card agenda-day">
      <div className="agenda-day-header">
        <span className="ncux-card-title">{day.label}</span>
        {day.is_today && <span className="ncux-badge ncux-badge-info">今天</span>}
        {day.holiday && <span className="ncux-badge ncux-badge-ok">停課</span>}
      </div>
      {day.holiday && <MetaLine icon="sun">{day.holiday}</MetaLine>}
      {day.events.map((event, i) => (
        <MetaLine key={`event-${i}`} icon={event.kind === "deadline" ? "alert" : "calendar"}>
          {event.title}
          {event.note && `（${event.note}）`}
          {EVENT_KINDS[event.kind] && (
            <span className={`ncux-badge ${EVENT_KINDS[event.kind][1]} agenda-inline-badge`}>{EVENT_KINDS[event.kind][0]}</span>
          )}
        </MetaLine>
      ))}
      {timed.map((item, i) =>
        item.type === "class" ? (
          <MetaLine key={`class-${i}`} icon="book">
            <span className="agenda-time">{item.time}</span>
            {item.title}
            {item.detail && <span className="agenda-detail">（{item.detail}）</span>}
          </MetaLine>
        ) : (
          <MetaLine key={`activity-${i}`} icon="pin">
            <span className="agenda-time">{item.time}</span>
            {item.title}
            {item.session && `・${item.session}`}
            {item.place && <span className="agenda-detail">（{item.place}）</span>}
          </MetaLine>
        ),
      )}
      {empty && <div className="ncux-card-meta">沒有排定的行程</div>}
    </div>
  );
}

/** 對應 kind === "personal_agenda"：課表、已報名的活動跟校曆整理成每天的行程（get_my_agenda）。 */
export default function PersonalAgenda({ data }) {
  const days = data.days || [];
  return (
    <div>
      <div className="ncux-card-title ncux-with-icon">
        <Icon name="calendar" size={18} />
        <span>{data.title}</span>
      </div>
      {days.map((day) => (
        <AgendaDay key={day.date} day={day} />
      ))}
      {(data.notes || []).map((note) => (
        <p key={note} className="ncux-card-meta ncux-meta-line">
          <Icon name="alert" />
          <span>{note}</span>
        </p>
      ))}
    </div>
  );
}
