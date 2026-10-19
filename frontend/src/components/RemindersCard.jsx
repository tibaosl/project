import { useEffect, useId, useState } from "react";
import { fetchReminders } from "../api/reminders";
import Icon from "./Icon";

// 登入後的背景準備還沒做完（status === "loading"）時，隔一段時間再問一次，最多問這麼多次（約 2 分鐘）
const POLL_MS = 2500;
const MAX_POLLS = 48;
// 收起來的狀態記在瀏覽器裡，重新整理、開新對話都照使用者上次的選擇
const COLLAPSED_KEY = "ncuxplore_reminders_collapsed";

const KIND_ICONS = { calendar: "calendar", activity: "users", scholarship: "coin", hours: "clock" };
const TONE_BADGES = { ok: "ncux-badge-ok", warn: "ncux-badge-warn", info: "ncux-badge-info" };

function loadCollapsed() {
  try {
    return localStorage.getItem(COLLAPSED_KEY) === "1";
  } catch {
    return false;
  }
}

function saveCollapsed(collapsed) {
  try {
    if (collapsed) localStorage.setItem(COLLAPSED_KEY, "1");
    else localStorage.removeItem(COLLAPSED_KEY);
  } catch {
    // localStorage 存不進去（例如私密瀏覽模式）就算了，只是重新整理後不會記得
  }
}

function ReminderBody({ item }) {
  return (
    <>
      <span className={`reminder-icon reminder-icon-${item.kind}`} aria-hidden="true">
        <Icon name={KIND_ICONS[item.kind] || "bell"} size={13} />
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
 * 開場畫面左上角的「最近要注意」小卡：校曆上接下來 7 天的截止日跟考試，有登入再加上已報名的活動、
 * 快截止的獎學金、等很久還沒核發的時數。助理不用等使用者開口就先提醒。
 * 點標題列可以收起來（只剩「最近要注意（3）」），項目多時清單限制高度、在卡片裡捲動。
 * 有後續問題可以問的項目（ask）可以點，點了就直接問。拿不到資料時整張卡片不顯示，不影響推薦問題。
 */
export default function RemindersCard({ token, isGuest, onAsk }) {
  const [data, setData] = useState(null);
  const [failed, setFailed] = useState(false);
  const [collapsed, setCollapsed] = useState(loadCollapsed);
  const bodyId = useId();

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
  const loadingParts = data?.loading || [];
  const busy = !data || loadingParts.length > 0;

  function toggle() {
    const next = !collapsed;
    setCollapsed(next);
    saveCollapsed(next);
  }

  return (
    <section
      className={`reminders${collapsed ? " is-collapsed" : ""}`}
      aria-label="最近要注意"
      aria-busy={busy}
    >
      <button
        type="button"
        className="reminders-toggle"
        aria-expanded={!collapsed}
        aria-controls={bodyId}
        onClick={toggle}
      >
        <Icon name="bell" size={15} className="reminders-bell" />
        <span className="reminders-title">
          {data?.title || "最近要注意"}
          {items.length > 0 && <span className="reminders-count">（{items.length}）</span>}
        </span>
        {collapsed && busy && <span className="dot" aria-hidden="true" />}
        <Icon name="chevron-down" size={15} className="reminders-chevron" />
      </button>

      <div id={bodyId} className="reminders-content" hidden={collapsed}>
        {data?.range && <p className="reminders-range">{data.range}</p>}

        {!data ? (
          <div className="reminders-skeleton" aria-hidden="true">
            <div />
            <div />
          </div>
        ) : (
          <>
            {items.length > 0 ? (
              <ul className="reminder-list">
                {items.map((item, i) => (
                  <li key={`${item.kind}-${item.title}-${i}`}>
                    {item.ask ? (
                      <button type="button" className="reminder-item is-clickable" onClick={() => onAsk(item.ask)}>
                        <ReminderBody item={item} />
                        <span className="reminder-arrow" aria-hidden="true">
                          <Icon name="arrow-right" size={13} />
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
      </div>
    </section>
  );
}
