import Icon, { MetaLine } from "./Icon";

export function ProgressBar({ ratio, done }) {
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
      ? `已達畢業學分門檻（${credits.earned} / ${credits.required} 學分）`
      : `已修 ${credits.earned} / ${credits.required} 學分，還差 ${credits.remaining} 學分`
    : `已修 ${credits.earned} 學分（畢業資格審查表暫時取不到，無法對照畢業門檻）`;

  return (
    <div className={`ncux-banner ncux-with-icon ${done ? "ncux-banner-ok" : "ncux-banner-warn"}`}>
      <Icon name={done ? "check-circle" : "alert"} size={18} />
      <div>
        <div>{headline}</div>
        <div style={{ fontSize: 12.5, fontWeight: 400, marginTop: 4 }}>
          {[department, grade].filter(Boolean).join("・")}
        </div>
      </div>
    </div>
  );
}

function Alerts({ alerts }) {
  if (!alerts.length) {
    return (
      <div className="ncux-card-meta ncux-with-icon" style={{ gap: 6 }}>
        <Icon name="check-circle" tone="ok" />
        <span>沒有不及格或停修、需要重修的課。</span>
      </div>
    );
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

function countText(label, earned, required) {
  // 沒有要求也還沒修到的（例如「特殊檢核」的學分 0、門數 0）不顯示，只看下面列的細項
  if (!earned && !required) return null;
  return `${label} ${earned}${required ? ` / ${required}` : ""}`;
}

function GraduationCategories({ categories }) {
  return categories.map((cat) => {
    const counts = [
      countText("學分", cat.earned_credits, cat.required_credits),
      countText("門數", cat.earned_courses, cat.required_courses),
    ]
      .filter(Boolean)
      .join("・");
    return (
      <div key={cat.name} style={{ marginBottom: 12 }}>
        <div style={{ fontSize: 13.5, marginBottom: 4 }}>
          <span className={`ncux-badge ${cat.passed ? "ncux-badge-ok" : "ncux-badge-warn"}`}>
            {cat.passed ? "完成" : "未完成"}
          </span>
          <span style={{ fontWeight: 600 }}>{cat.name}</span>
          {counts && <span className="ncux-card-meta"> {counts}</span>}
        </div>
        <ProgressBar ratio={cat.percentage / 100} done={cat.passed} />
        {cat.unmet_rules.map((rule) => (
          <div key={rule.name} style={{ fontSize: 12.5, marginTop: 4, color: "var(--ncux-text-muted)" }}>
            ・{rule.name}：{remainingText(rule)}
            {rule.memo && `（${rule.memo}）`}
          </div>
        ))}
      </div>
    );
  });
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

/** 「GPA 3.85（4.3 制）」：iNCU 沒有 GPA，是照學校的對照表自己算的，跟學校的累計平均對不上時標註。 */
function gpaText(gpa) {
  if (gpa.gpa == null) return "";
  const note = gpa.gpa_matches_school_average === false ? "，算法跟學校的累計平均有出入，僅供參考" : "";
  return `GPA ${gpa.gpa.toFixed(2)}（4.3 制${note}）`;
}

function GpaTable({ semesters }) {
  const hasGpa = semesters.some((s) => s.gpa != null);
  return (
    <Table headers={["學期", "學期平均", ...(hasGpa ? ["GPA"] : []), "變化", "實得學分", "班排名", "系排名"]}>
      {semesters.map((s, i) => {
        const prev = i > 0 ? semesters[i - 1].average : null;
        const change = s.average != null && prev != null ? s.average - prev : null;
        return (
          <tr key={s.term}>
            <td style={CELL}>{s.label}</td>
            <td style={CELL}>{formatScore(s.average)}</td>
            {hasGpa && <td style={CELL}>{formatScore(s.gpa)}</td>}
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
      {gpa.gpa != null && <div className="ncux-card-meta">{gpaText(gpa)}</div>}
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

/** 成績的區塊附一行試算：這學期還沒有成績的課全拿 A、全拿 B 的累計 GPA，順便讓使用者知道可以問試算。 */
function EstimateTeaser({ estimate }) {
  const pick = (grade) => estimate?.scenarios?.find((s) => s.grade === grade);
  const [a, b] = [pick("A"), pick("B")];
  if (!a || !b) return null;
  return (
    <MetaLine icon="calculator" className="ncux-estimate-teaser">
      {`${estimate.scope_text}還沒有成績，全拿 A 累計 GPA 會是 ${a.gpa.toFixed(2)}，全拿 B 會是 ${b.gpa.toFixed(2)}。`}
      {"想試算別的情況可以問我，例如「要考幾分累計 GPA 才到 3.8」。"}
    </MetaLine>
  );
}

function changeCell(change) {
  if (Math.abs(change) < 0.005) return { text: "不變" };
  return {
    text: `${change > 0 ? "▲" : "▼"} ${Math.abs(change).toFixed(2)}`,
    color: change > 0 ? "var(--ncux-ok-text)" : "var(--ncux-danger-text)",
  };
}

/**
 * GPA 試算（estimate_my_gpa）：上面是回答使用者問題的句子（假設拿某個成績、要到某個目標），
 * 下面是剩下的課都拿 A+ 到 B- 各會變成多少累計 GPA。estimate 是後端 estimate_gpa 的結果。
 */
function GpaEstimate({ estimate }) {
  if (!estimate) {
    return (
      <div className="ncux-banner ncux-banner-warn ncux-with-icon">
        <Icon name="alert" size={18} />
        <div>成績單上還沒有算得進 GPA 的成績，沒辦法試算。</div>
      </div>
    );
  }
  const courses = estimate.remaining_courses ?? [];
  return (
    <div>
      {estimate.answers.length > 0 && (
        <div className="ncux-banner ncux-banner-info ncux-with-icon">
          <Icon name="calculator" size={18} />
          <div>
            {estimate.answers.map((answer) => (
              <div key={answer}>{answer}</div>
            ))}
          </div>
        </div>
      )}
      <div className="ncux-card">
        <div className="ncux-card-title">
          目前累計 GPA {estimate.current_gpa.toFixed(2)}・累計平均 {formatScore(estimate.current_average)}
        </div>
        <div className="ncux-card-meta">
          4.3 制，照教務處的等第對照表換算，算進 GPA 的有 {estimate.credits} 學分
        </div>
        {courses.length > 0 && (
          <div className="ncux-card-meta">
            這學期還沒有成績的課：{courses.map((c) => `${c.name} ${c.credits} 學分`).join("、")}
          </div>
        )}
      </div>
      {estimate.scenarios.length > 0 && (
        <Section title={`${estimate.scope_text}每科都拿同一個等第的話`}>
          <Table headers={["每科都拿", "累計 GPA", "跟現在比"]}>
            {estimate.scenarios.map((s) => {
              const change = changeCell(s.gpa - estimate.current_gpa);
              return (
                <tr key={s.grade}>
                  <td style={CELL}>{s.label}</td>
                  <td style={CELL}>{s.gpa.toFixed(2)}</td>
                  <td style={{ ...CELL, color: change.color }}>{change.text}</td>
                </tr>
              );
            })}
          </Table>
        </Section>
      )}
      {estimate.notes.map((note) => (
        <div key={note} className="ncux-card-meta" style={{ marginTop: 6 }}>
          ※ {note}
        </div>
      ))}
    </div>
  );
}

/**
 * data：get_my_academic_analysis、estimate_my_gpa 工具回傳的 {kind: "academic_analysis", ...}。
 * data.focus 決定顯示哪一塊，只回答使用者問的：credits（學分/畢業缺口）、
 * grades（成績/排名）、overview（全部）、estimate（GPA 試算）。
 */
export default function AcademicAnalysis({ data }) {
  const focus = data.focus || "overview";
  if (focus === "estimate") {
    return (
      <div>
        <GpaEstimate estimate={data.estimate} />
        <div className="ncux-card-meta" style={{ marginTop: 6 }}>
          ※ 用 iNCU 成績單上的成績試算，假設剩下的課都照成績單上的學分算進 GPA，實際以教務處計算為準。
        </div>
      </div>
    );
  }
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
            {data.gpa.gpa != null && `、${gpaText(data.gpa)}`}
            {data.gpa.latest_change != null &&
              `，最近一學期比前一學期${data.gpa.latest_change >= 0 ? "進步" : "退步"} ${Math.abs(
                data.gpa.latest_change,
              ).toFixed(2)} 分`}
          </div>
          <GpaTable semesters={data.gpa.semesters} />
          <EstimateTeaser estimate={data.estimate} />
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
