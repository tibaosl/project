import { useEffect, useState } from "react";
import { fetchReminders } from "../api/reminders";
import Icon from "./Icon";

// 登入後的背景準備還沒做完（status === "loading"）時，隔一段時間再問一次，最多問這麼多次（約 2 分鐘）
const POLL_MS = 2500;
const MAX_POLLS = 48;
const COLLAPSED_COUNT = 5;

const KIND_ICONS = { calendar: "calendar", activity: "users", scholarship: "coin", hours: "clock" };
const TONE_BADGES = { ok: "ncux-badge-ok", warn: "ncux-badge-warn", info: "ncux-badge-info" };

function ReminderBody({ item }) {
  return (
    <>
      <span className={`reminder-icon reminder-icon-${item.kind}`} aria-hidden="true">
        <Icon name={KIND_ICONS[item.kind] || "bell"} size={16} />
      </span>
      <span className="reminder-body">
        <span className="reminder-head">
          {item.when && <span className="reminder-when">{item.when}</span>}
          <span className={`ncux-badge reminder-badge ${TONE_BADGES[item.tone] || "reminder-badge-plain"}`}>
            {item.badge}
          </span>
        </span>
        <span className="reminder-title">{item.title}</span>
        {item.detail && <span className="reminder-detail">{item.detail}</span>}
        {item.hint && <span className="reminder-hint">{item.hint}</span>}
      </span>
    </>
  );
}

/**
 * 開場畫面的「最近要注意」：校曆上接下來 7 天的截止日跟考試，有登入再加上已報名的活動、
 * 快截止的獎學金、等很久還沒核發的時數。助理不用等使用者開口就先提醒。
 * 有後續問題可以問的項目（ask）可以點，點了就直接問。拿不到資料時整張卡片不顯示，不影響推薦問題。
 */
export default function RemindersCard({ token, isGuest, onAsk }) {
  const [data, setData] = useState(null);
  const [failed, setFailed] = useState(false);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let timer;
    let polls = 0;

    async function load() {
      try {
        const result = await fetchReminders(token);
        if (cancelled) return;
        setData(result);
        setFailed(false);
        if (result.status === "loading" && polls < MAX_POLLS) {
          polls += 1;
          timer = setTimeout(load, POLL_MS);
        }
      } catch {
        if (!cancelled) setFailed(true);
      }
    }

    setData(null);
    load();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [token]);

  if (failed) return null;

  const items = data?.items || [];
  const shown = expanded ? items : items.slice(0, COLLAPSED_COUNT);
  const loadingParts = data?.loading || [];

  return (
    <section className="reminders" aria-label="最近要注意" aria-busy={!data || loadingParts.length > 0}>
      <div className="reminders-header">
        <span className="reminders-title ncux-with-icon">
          <Icon name="bell" size={18} />
          <span>{data?.title || "最近要注意"}</span>
        </span>
        {data?.range && <span className="reminders-range">{data.range}</span>}
      </div>

      {!data ? (
        <div className="reminders-skeleton" aria-hidden="true">
          <div />
          <div />
        </div>
      ) : (
        <>
          {items.length > 0 ? (
            <ul className="reminder-list">
              {shown.map((item, i) => (
                <li key={`${item.kind}-${item.title}-${i}`}>
                  {item.ask ? (
                    <button type="button" className="reminder-item is-clickable" onClick={() => onAsk(item.ask)}>
                      <ReminderBody item={item} />
                      <span className="reminder-arrow" aria-hidden="true">
                        <Icon name="arrow-right" size={14} />
                      </span>
                    </button>
                  ) : (
                    <div className="reminder-item">
                      <ReminderBody item={item} />
                    </div>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            loadingParts.length === 0 && <p className="reminders-empty">接下來 7 天沒有要特別注意的事。</p>
          )}

          {items.length > COLLAPSED_COUNT && (
            <button type="button" className="reminders-more" onClick={() => setExpanded((value) => !value)}>
              {expanded ? "收起來" : `再看 ${items.length - COLLAPSED_COUNT} 項`}
            </button>
          )}

          {loadingParts.length > 0 && (
            <p className="reminders-loading" role="status">
              <span className="dot" aria-hidden="true" />
              正在整理你的{loadingParts.join("、")}…
            </p>
          )}
          {(data.notes || []).map((note) => (
            <p key={note} className="reminders-note">{note}</p>
          ))}
          {isGuest && (
            <p className="reminders-note">登入後還會提醒你報名的活動、快截止的獎學金跟還沒核發的時數。</p>
          )}
        </>
      )}
    </section>
  );
}
