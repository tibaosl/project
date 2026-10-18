import Icon, { MetaLine } from "./Icon";
import { ProgressBar } from "./AcademicAnalysis";

// 每一項的狀態（後端 graduation_check.py 的 DONE、THIS_TERM…）
const STATUS = {
  todo: { label: "還要補", badge: "ncux-badge-warn" },
  this_term: { label: "這學期修完就好", badge: "ncux-badge-info" },
  pending: { label: "等時數核發", badge: "ncux-badge-info" },
  auto: { label: "照常在學就好", badge: "ncux-badge-info" },
  done: { label: "完成", badge: "ncux-badge-ok" },
};

function ItemCard({ item, onAsk }) {
  const status = STATUS[item.status] || STATUS.todo;
  // 「共同必修學分（整體）」這種標題已經有類別名稱，不用再寫一次
  const meta = [!item.title.startsWith(item.group) && item.group, item.progress].filter(Boolean).join("・");
  return (
    <div className="ncux-card" style={{ padding: "10px 14px" }}>
      <div style={{ fontSize: 13.5 }}>
        <span className={`ncux-badge ${status.badge}`}>{status.label}</span>
        <span style={{ fontWeight: 600 }}>{item.title}</span>
        {meta && <span className="ncux-card-meta"> {meta}</span>}
      </div>
      {item.ratio != null && (
        <div style={{ marginTop: 6 }}>
          <ProgressBar ratio={item.ratio} done={false} />
        </div>
      )}
      {item.lines.map((line) => (
        <div key={line} style={{ fontSize: 13, marginTop: 4 }}>
          ・{line}
        </div>
      ))}
      {item.how && (
        <MetaLine icon="bulb" className="graduation-how">
          {item.how}
        </MetaLine>
      )}
      {onAsk && item.asks.length > 0 && (
        <div className="follow-ups-list" style={{ marginTop: 8 }}>
          {item.asks.map((ask) => (
            <button type="button" key={ask.question} className="follow-up-chip" onClick={() => onAsk(ask.question)}>
              {ask.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function DoneList({ items }) {
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: "6px 16px" }}>
      {items.map((item) => (
        <span key={item.title} className="ncux-with-icon" style={{ fontSize: 13, gap: 5 }}>
          <Icon name="check-circle" tone="ok" />
          <span>
            {item.title}
            {item.note && <span className="ncux-card-meta">（{item.note}）</span>}
          </span>
        </span>
      ))}
    </div>
  );
}

/**
 * data：check_my_graduation 工具回傳的 {kind: "graduation_check", ...}。
 * 還沒完成的條件一項一張小卡（還差多少、這學期修課中的課、怎麼補），完成的只列名稱。
 * onAsk：點「問必修科目有哪些」這類按鈕時送出問題（審查表沒寫細節的條件，點了才查法規）。
 */
export default function GraduationCheck({ data, onAsk }) {
  const unfinished = data.items.filter((item) => item.status !== "done");
  const done = data.items.filter((item) => item.status === "done");
  const who = [data.department, data.cohort && `${data.cohort} 學年度入學`].filter(Boolean).join("・");

  return (
    <div>
      <div className={`ncux-banner ncux-with-icon ${data.can_graduate ? "ncux-banner-ok" : "ncux-banner-warn"}`}>
        <Icon name={data.can_graduate ? "check-circle" : "cap"} size={18} />
        <div>
          <div>{data.headline}</div>
          <div style={{ fontSize: 12.5, fontWeight: 400, marginTop: 4 }}>{data.summary}</div>
          {who && <div style={{ fontSize: 12.5, fontWeight: 400, marginTop: 2 }}>{who}</div>}
        </div>
      </div>

      {unfinished.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <div className="ncux-card-title">還沒完成的條件</div>
          {unfinished.map((item) => (
            <ItemCard key={`${item.group}-${item.title}`} item={item} onAsk={onAsk} />
          ))}
        </div>
      )}

      {done.length > 0 && (
        <div style={{ marginTop: 14 }}>
          <div className="ncux-card-title">已完成</div>
          <DoneList items={done} />
        </div>
      )}

      <div className="ncux-card-meta" style={{ marginTop: 12 }}>
        ※ 資料來自 iNCU 畢業資格審查表跟學習護照時數，僅供參考，實際以各學系跟教務處的審查為準。
      </div>
    </div>
  );
}
