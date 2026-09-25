function ProgressBar({ ratio, done }) {
  return (
    <div style={{ height: 8, borderRadius: 999, background: "var(--ncux-border)", overflow: "hidden" }}>
      <div
        style={{
          height: "100%",
          width: `${Math.max(0, Math.min(1, ratio)) * 100}%`,
          background: done ? "var(--ncux-ok-text)" : "var(--ncux-accent)",
        }}
      />
    </div>
  );
}

function remainingText(rule) {
  const parts = [];
  if (rule.remaining_credits) parts.push(`${rule.remaining_credits} 學分`);
  if (rule.remaining_courses) parts.push(`${rule.remaining_courses} 門`);
  return parts.length ? `還差 ${parts.join("、")}` : "條件尚未達成";
}

function CreditsBanner({ credits, department, grade, graduationAvailable }) {
  const done = credits.passed_threshold === true;
  const headline = graduationAvailable
    ? done
      ? `✅ 已達畢業學分門檻（${credits.earned} / ${credits.required} 學分）`
      : `⚠️ 已修 ${credits.earned} / ${credits.required} 學分，還差 ${credits.remaining} 學分`
    : `已修 ${credits.earned} 學分（畢業資格審查表暫時取不到，無法對照畢業門檻）`;

  return (
    <div className={`ncux-banner ${done ? "ncux-banner-ok" : "ncux-banner-warn"}`}>
      <div>{headline}</div>
      <div style={{ fontSize: 12.5, fontWeight: 400, marginTop: 4 }}>
        {[department, grade].filter(Boolean).join("・")}
      </div>
    </div>
  );
}

function Alerts({ alerts }) {
  if (!alerts.length) {
    return <div className="ncux-card-meta">✅ 沒有不及格或停修、需要重修的課。</div>;
  }
  return alerts.map((a) => (
    <div key={`${a.term}-${a.course_no}`} className="ncux-card" style={{ padding: "10px 14px" }}>
      <span className={`ncux-badge ${a.required ? "ncux-badge-warn" : "ncux-badge-info"}`}>
        {a.required ? "必修" : a.course_type}
      </span>
      <span style={{ fontWeight: 600 }}>{a.name}</span>
      <span className="ncux-card-meta">
        {" "}
        {a.label}・{a.course_no}・{a.credits} 學分
      </span>
      <div style={{ fontSize: 13, marginTop: 4 }}>
        {a.reason}
        {a.score_text !== a.reason && `（${a.score_text}）`}
        {a.required && "，必修課需要重新修習才能畢業"}
      </div>
    </div>
  ));
}

function GraduationCategories({ categories }) {
  return categories.map((cat) => (
    <div key={cat.name} style={{ marginBottom: 12 }}>
      <div style={{ fontSize: 13.5, marginBottom: 4 }}>
        <span className={`ncux-badge ${cat.passed ? "ncux-badge-ok" : "ncux-badge-warn"}`}>
          {cat.passed ? "完成" : "未完成"}
        </span>
        <span style={{ fontWeight: 600 }}>{cat.name}</span>
        <span className="ncux-card-meta">
          {" "}
          學分 {cat.earned_credits}
          {cat.required_credits ? ` / ${cat.required_credits}` : ""}・門數 {cat.earned_courses}
          {cat.required_courses ? ` / ${cat.required_courses}` : ""}
        </span>
      </div>
      <ProgressBar ratio={cat.percentage / 100} done={cat.passed} />
      {cat.unmet_rules.map((rule) => (
        <div key={rule.name} style={{ fontSize: 12.5, marginTop: 4, color: "var(--ncux-text-muted)" }}>
          ・{rule.name}：{remainingText(rule)}
          {rule.memo && `（${rule.memo}）`}
        </div>
      ))}
    </div>
  ));
}

const CELL = { padding: "6px 8px", borderBottom: "1px solid var(--ncux-border)", whiteSpace: "nowrap" };

function Table({ headers, children }) {
  return (
    <div style={{ overflowX: "auto" }}>
      <table style={{ borderCollapse: "collapse", width: "100%", fontSize: 13 }}>
        <thead>
          <tr style={{ color: "var(--ncux-text-muted)", textAlign: "left" }}>
            {headers.map((h) => (
              <th key={h} style={CELL}>
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

function formatScore(value) {
  return value != null ? value.toFixed(2) : "—";
}

function GpaTable({ semesters }) {
  return (
    <Table headers={["學期", "學期平均", "變化", "實得學分", "班排名", "系排名"]}>
      {semesters.map((s, i) => {
        const prev = i > 0 ? semesters[i - 1].average : null;
        const change = s.average != null && prev != null ? s.average - prev : null;
        return (
          <tr key={s.term}>
            <td style={CELL}>{s.label}</td>
            <td style={CELL}>{formatScore(s.average)}</td>
            <td
              style={{
                ...CELL,
                color: change == null ? undefined : change >= 0 ? "var(--ncux-ok-text)" : "var(--ncux-danger-text)",
              }}
            >
              {change == null ? "—" : `${change >= 0 ? "▲" : "▼"} ${Math.abs(change).toFixed(2)}`}
            </td>
            <td style={CELL}>{s.earned_credits ?? "—"}</td>
            <td style={CELL}>{s.class_rank ?? "—"}</td>
            <td style={CELL}>{s.dept_rank ?? "—"}</td>
          </tr>
        );
      })}
    </Table>
  );
}

function CumulativeRankTable({ ranks }) {
  return (
    <Table headers={["截至學期", "累計平均", "累計班排名", "累計系排名"]}>
      {ranks.map((r) => (
        <tr key={r.term}>
          <td style={CELL}>{r.label}</td>
          <td style={CELL}>{formatScore(r.average)}</td>
          <td style={CELL}>{r.class_rank ?? "—"}</td>
          <td style={CELL}>{r.dept_rank ?? "—"}</td>
        </tr>
      ))}
    </Table>
  );
}

function GradesHeader({ gpa, department, grade }) {
  const latest = gpa.cumulative_ranks?.at(-1);
  return (
    <div className="ncux-card">
      <div className="ncux-card-title">
        累計學業平均 {formatScore(gpa.cumulative_average)}
        {latest && `・累計排名 班 ${latest.class_rank}、系 ${latest.dept_rank}`}
      </div>
      <div className="ncux-card-meta">
        {[department, grade, latest && `排名截至 ${latest.label}`].filter(Boolean).join("・")}
      </div>
    </div>
  );
}

function Section({ title, children }) {
  return (
    <div style={{ marginTop: 16 }}>
      <div className="ncux-card-title">{title}</div>
      {children}
    </div>
  );
}

/**
 * data：get_my_academic_analysis 工具回傳的 {kind: "academic_analysis", ...}。
 * data.focus 決定顯示哪一塊，只回答使用者問的：credits（學分/畢業缺口）、
 * grades（成績/排名）、overview（全部）。
 */
export default function AcademicAnalysis({ data }) {
  const focus = data.focus || "overview";
  const showCredits = focus !== "grades";
  const showGrades = focus !== "credits";
  const byType = Object.entries(data.credits.by_course_type || {});
  // 只問學分時，只提會卡畢業的必修；一般選修被當不影響畢業學分缺口的回答
  const alerts = showGrades ? data.alerts : data.alerts.filter((a) => a.required);
  const cumulativeRanks = data.gpa.cumulative_ranks || [];

  return (
    <div>
      {showCredits ? (
        <CreditsBanner
          credits={data.credits}
          department={data.department}
          grade={data.grade}
          graduationAvailable={data.graduation_available}
        />
      ) : (
        <GradesHeader gpa={data.gpa} department={data.department} grade={data.grade} />
      )}

      {(showGrades || alerts.length > 0) && (
        <Section title={showGrades ? "需要注意的課" : "尚未通過的必修"}>
          <Alerts alerts={alerts} />
        </Section>
      )}

      {showCredits && data.graduation_available && (
        <Section title="畢業類別進度">
          <GraduationCategories categories={data.graduation_categories} />
        </Section>
      )}

      {showGrades && (
        <Section title="成績趨勢">
          <div className="ncux-card-meta" style={{ marginBottom: 6 }}>
            累計學業平均 {formatScore(data.gpa.cumulative_average)}
            {data.gpa.latest_change != null &&
              `，最近一學期比前一學期${data.gpa.latest_change >= 0 ? "進步" : "退步"} ${Math.abs(
                data.gpa.latest_change,
              ).toFixed(2)} 分`}
          </div>
          <GpaTable semesters={data.gpa.semesters} />
        </Section>
      )}

      {showGrades && cumulativeRanks.length > 0 && (
        <Section title="累計排名">
          <CumulativeRankTable ranks={cumulativeRanks} />
        </Section>
      )}

      {showCredits && byType.length > 0 && (
        <div className="ncux-card-meta" style={{ marginTop: 10 }}>
          已通過學分（依成績單課程屬性）：{byType.map(([type, credits]) => `${type} ${credits}`).join("、")}
        </div>
      )}

      <div className="ncux-card-meta" style={{ marginTop: 6 }}>
        ※ 資料來自 iNCU 成績查詢與畢業資格審查表，僅供參考，實際以教務處審查為準。
      </div>
    </div>
  );
}
