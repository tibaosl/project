export default function HoursDashboard({ data }) {
  const graduated = data.graduated;
  const categories = data.categories || {};

  return (
    <div>
      <div className={`ncux-banner ${graduated ? "ncux-banner-ok" : "ncux-banner-warn"}`}>
        {graduated ? "✅ 學習護照時數已達畢業門檻！" : "⚠️ 學習護照時數尚未達畢業門檻"}
      </div>

      {Object.entries(categories).map(([group, cat]) => {
        const passed = cat.passed_graduation;
        return (
          <div key={group} style={{ marginBottom: 14 }}>
            <div className="ncux-card" style={{ padding: "8px 16px", marginBottom: 4 }}>
              <span className="ncux-card-title">{group}</span>{" "}
              <span className={`ncux-badge ${passed ? "ncux-badge-ok" : "ncux-badge-warn"}`}>
                {passed ? "已達標" : "尚未達標"}
              </span>
              <span className="ncux-card-meta"> 門檻總計 {cat.graduation_required} 小時</span>
            </div>

            {Object.entries(cat.subcategories || {}).map(([subName, sub]) => {
              const required = sub.required || 0;
              const confirmed = sub.confirmed_hours || 0;
              const ratio = required ? Math.min(1, confirmed / required) : 1;
              return (
                <div key={subName} style={{ marginBottom: 8 }}>
                  <div style={{ fontSize: 13, marginBottom: 4 }}>
                    {sub.passed ? "✅" : "⚠️"} {subName}：{confirmed}/{required} 小時
                    {!sub.passed && `（還差 ${sub.remaining} 小時）`}
                    {sub.pending_hours ? `，另有 ${sub.pending_hours} 小時待核發` : ""}
                  </div>
                  <div
                    style={{
                      height: 8,
                      borderRadius: 999,
                      background: "var(--ncux-border)",
                      overflow: "hidden",
                    }}
                  >
                    <div
                      style={{
                        height: "100%",
                        width: `${ratio * 100}%`,
                        background: sub.passed ? "var(--ncux-ok-text)" : "var(--ncux-accent)",
                      }}
                    />
                  </div>
                </div>
              );
            })}
          </div>
        );
      })}
    </div>
  );
}
