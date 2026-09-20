const PERIODS = [
  ["第一節", "08:00-08:50"],
  ["第二節", "09:00-09:50"],
  ["第三節", "10:00-10:50"],
  ["第四節", "11:00-11:50"],
  ["中午", "12:00-12:50"],
  ["第五節", "13:00-13:50"],
  ["第六節", "14:00-14:50"],
  ["第七節", "15:00-15:50"],
  ["第八節", "16:00-16:50"],
  ["第九節", "17:00-17:50"],
  ["第Ａ節", "18:00-18:50"],
  ["第Ｂ節", "19:00-19:50"],
  ["第Ｃ節", "20:00-20:50"],
];
const DAYS = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六"];
const ALWAYS_SHOW = new Set(["第一節", "第二節", "第三節", "第四節", "第五節", "第六節", "第七節", "第八節"]);

function normalizePeriod(period) {
  return (period || "")
    .replace("第A節", "第Ａ節")
    .replace("第B節", "第Ｂ節")
    .replace("第C節", "第Ｃ節");
}

function chunkDetails(details) {
  const blocks = [];
  for (let i = 0; i < details.length; i += 3) {
    blocks.push(details.slice(i, i + 3));
  }
  return blocks;
}

/** courses：get_my_schedule 工具直接回傳的原始陣列（day/period/time/details）。 */
export default function ScheduleTable({ courses }) {
  const grid = new Map();
  for (const item of courses) {
    const p = normalizePeriod(item.period);
    const d = (item.day || "").trim();
    if (!p || !d) continue;
    const key = `${p}|${d}`;
    const existing = grid.get(key) || [];
    grid.set(key, existing.concat(item.details || []));
  }

  const visiblePeriods = PERIODS.filter(
    ([name]) => ALWAYS_SHOW.has(name) || DAYS.some((d) => grid.has(`${name}|${d}`))
  );

  return (
    <div className="timetable-wrap">
      <table className="timetable">
        <thead>
          <tr>
            <th>節次 / 時間</th>
            {DAYS.map((d) => (
              <th key={d}>{d}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {visiblePeriods.map(([name, time]) => (
            <tr key={name}>
              <td className="time-cell">
                {name}
                <br />
                <span style={{ fontSize: 10, fontWeight: 400, color: "var(--ncux-text-muted)" }}>{time}</span>
              </td>
              {DAYS.map((d) => {
                const details = grid.get(`${name}|${d}`);
                return (
                  <td className={details ? "has-course" : undefined} key={d}>
                    {details &&
                      chunkDetails(details).map((block, i) => (
                        <div key={i} style={{ marginBottom: i < chunkDetails(details).length - 1 ? 6 : 0 }}>
                          {block.length === 3 && (
                            <>
                              <div className="course-code">{block[0]}</div>
                              <div className="course-title">{block[1]}</div>
                              <div className="course-location">{block[2]}</div>
                            </>
                          )}
                          {block.length === 2 && (
                            <>
                              <div className="course-title">{block[0]}</div>
                              <div className="course-location">{block[1]}</div>
                            </>
                          )}
                          {block.length === 1 && <div className="course-title">{block[0]}</div>}
                        </div>
                      ))}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
