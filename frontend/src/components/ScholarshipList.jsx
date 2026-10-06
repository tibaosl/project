import Icon, { MetaLine } from "./Icon";

// 「還要確認」的獎學金依要確認的條件分組：有好幾個條件時歸到排在前面的那組（身分條件在前，
// 例如同時要經濟弱勢跟語言檢定的，沒有經濟弱勢身分就不用看了），組的順序也照這個
const PENDING_ORDER = [
  "經濟弱勢",
  "原住民",
  "新住民子女",
  "身心障礙",
  "設籍地區",
  "運動表現",
  "幹部或服務",
  "競賽或研究表現",
  "語言檢定",
  "出國交流",
  "其他",
  "資料不足",
];

const PENDING_LABEL = {
  經濟弱勢: "要有經濟弱勢身分（清寒、低收入戶等）",
  原住民: "要有原住民身分",
  新住民子女: "要是新住民子女",
  身心障礙: "要有身心障礙身分",
  設籍地區: "要設籍在特定地區",
  運動表現: "要有運動表現",
  幹部或服務: "要有幹部或服務經歷",
  競賽或研究表現: "要有競賽或研究表現",
  語言檢定: "要通過語言檢定",
  出國交流: "要出國交流",
  其他: "其他條件",
  資料不足: "成績資料不夠判斷",
};

function CheckMark({ ok }) {
  if (ok === true) return <Icon name="check" tone="ok" />;
  if (ok === false) return <Icon name="x" tone="danger" />;
  return <Icon name="question" tone="warn" />;
}

function CheckLine({ ok, children }) {
  return (
    <li className="ncux-with-icon" style={{ gap: 6 }}>
      <CheckMark ok={ok} />
      <span>{children}</span>
    </li>
  );
}

function deadlineText(item) {
  if (item.deadline && item.deadline_term) {
    if (!/\d/.test(item.deadline)) return `${item.deadline_term} 學期：${item.deadline}`;
    return `${item.deadline_term} 學期截止日 ${item.deadline}${item.deadline_passed ? "（已截止）" : ""}`;
  }
  return item.period ? `申請期間：${item.period}` : "";
}

function fileHref(file) {
  // 爬蟲抓的文件放在 data/<來源>/ 底下，路徑的斜線要留著，每一段各自編碼（同 SourcesPanel）
  return `/files/${file.split("/").map(encodeURIComponent).join("/")}`;
}

function Sources({ sources }) {
  if (!sources?.length) return null;
  return (
    <div className="ncux-card-meta" style={{ marginTop: 6 }}>
      {sources.slice(0, 2).map((s) => (
        <span key={s.file} style={{ marginRight: 12 }}>
          <a href={fileHref(s.file)} target="_blank" rel="noreferrer">
            <Icon name="file" /> {s.title}
          </a>
          {s.url && (
            <>
              {" "}
              <a href={s.url} target="_blank" rel="noreferrer">
                （官網）
              </a>
            </>
          )}
        </span>
      ))}
    </div>
  );
}

export function ScholarshipCard({ item }) {
  const deadline = deadlineText(item);
  const money = [item.amount, item.quota && `名額 ${item.quota}`].filter(Boolean).join("・");
  // 分獎項的卡片：各獎項自己的條件寫在獎項旁邊，下面只列大家都要確認的
  const tierConditions = new Set((item.tiers ?? []).flatMap((t) => t.conditions ?? []));
  const pending = item.pending.filter((p) => !tierConditions.has(p.text));
  return (
    <div className="ncux-card">
      <div className="ncux-card-title">
        {item.name} <span className="ncux-badge ncux-badge-info">{item.kind}</span>
        {item.how_to_apply?.startsWith("免申請") && <span className="ncux-badge ncux-badge-ok">免申請</span>}
      </div>
      {money && <MetaLine icon="coin">{money}</MetaLine>}
      {item.tiers?.length > 0 && (
        <ul className="ncux-card-meta" style={{ margin: "2px 0", paddingLeft: 20 }}>
          {item.tiers.map((t) => (
            <li key={t.name}>
              {t.name}
              {t.amount && `：${t.amount}`}
              {t.conditions?.length > 0 && `（條件：${t.conditions.join("、")}）`}
            </li>
          ))}
        </ul>
      )}
      {deadline && <MetaLine icon="calendar">{deadline}</MetaLine>}
      <ul style={{ listStyle: "none", padding: 0, margin: "6px 0 4px", fontSize: 13, lineHeight: 1.7 }}>
        {item.checks.map((c) => (
          <CheckLine key={c.label} ok={c.ok}>
            {c.label}
          </CheckLine>
        ))}
        {pending.map((p) => (
          <CheckLine key={p.text} ok={null}>
            要確認：{p.text}
          </CheckLine>
        ))}
        {pending.length < item.pending.length && <CheckLine ok={null}>要確認：上面各獎項的條件</CheckLine>}
      </ul>
      {item.conduct_min > 0 && (
        <div className="ncux-card-meta">另外要操行 {item.conduct_min} 分以上（成績單上沒有操行成績，請自己確認）</div>
      )}
      {item.how_to_apply && <MetaLine icon="form">{item.how_to_apply}</MetaLine>}
      {item.notes && <div className="ncux-card-meta">※ {item.notes}</div>}
      <Sources sources={item.sources} />
    </div>
  );
}

const pendingRank = (kind) => (PENDING_ORDER.includes(kind) ? PENDING_ORDER.indexOf(kind) : PENDING_ORDER.length);

function groupByPending(items) {
  const groups = new Map();
  for (const item of items) {
    const kinds = item.pending.map((p) => p.kind).sort((a, b) => pendingRank(a) - pendingRank(b));
    const kind = kinds[0] || "資料不足";
    if (!groups.has(kind)) groups.set(kind, []);
    groups.get(kind).push(item);
  }
  return [...groups.entries()].sort(([a], [b]) => pendingRank(a) - pendingRank(b));
}

function profileText(profile) {
  const grade = profile.grade ? `${profile.grade} 年級` : "";
  const latest =
    profile.latest_average != null
      ? `${profile.latest_term} 平均 ${profile.latest_average.toFixed(2)}${
          profile.latest_class_rank ? `（班排名 ${profile.latest_class_rank}）` : ""
        }`
      : "";
  const year = profile.year_average != null ? `${profile.previous_year} 學年平均 ${profile.year_average.toFixed(2)}` : "";
  return [profile.department, [profile.degree, grade].filter(Boolean).join(" "), latest, year].filter(Boolean).join("・");
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
 * data：recommend_scholarships_for_me 工具回傳的 {kind: "scholarship_recommendations", ...}。
 * eligible 是成績單看得出來、條件都符合的；maybe 是成績、年級符合，但還要確認身分或其他條件的。
 */
export default function ScholarshipList({ data }) {
  const { profile, eligible, maybe } = data;
  const headline = eligible.length
    ? `有 ${eligible.length} 項獎學金你看起來符合資格`
    : "目前沒有找到從成績單就能確定符合的獎學金";
  const closed = eligible.filter((item) => item.deadline_passed).length;

  return (
    <div>
      <div className={`ncux-banner ncux-with-icon ${eligible.length ? "ncux-banner-ok" : "ncux-banner-warn"}`}>
        <Icon name={eligible.length ? "cap" : "alert"} size={18} />
        <div>
          <div>
            {headline}
            {maybe.length > 0 && `，另外 ${maybe.length} 項還要確認條件`}
          </div>
          <div style={{ fontSize: 12.5, fontWeight: 400, marginTop: 4 }}>{profileText(profile)}</div>
          {closed > 0 && (
            <div style={{ fontSize: 12.5, fontWeight: 400 }}>
              其中 {closed} 項這學期的申請已經截止，可以留意下一次的公告。
            </div>
          )}
          {data.statuses?.length > 0 && (
            <div style={{ fontSize: 12.5, fontWeight: 400 }}>已經算進你說明的身分：{data.statuses.join("、")}</div>
          )}
        </div>
      </div>

      {eligible.length > 0 && (
        <Section title={`符合資格（${eligible.length}）`}>
          {eligible.map((item) => (
            <ScholarshipCard key={item.name} item={item} />
          ))}
        </Section>
      )}

      {maybe.length > 0 && (
        <Section title={`還要確認條件（${maybe.length}）`}>
          <div className="ncux-card-meta" style={{ marginBottom: 6 }}>
            成績、年級、系所看起來符合，但還有成績單看不出來的條件。符合的話可以跟我說（例如「我是低收入戶」），我再幫你重新比對。
          </div>
          {groupByPending(maybe).map(([kind, items]) => (
            <details key={kind} style={{ margin: "6px 0" }}>
              <summary style={{ cursor: "pointer", fontSize: 13.5, fontWeight: 600 }}>
                {PENDING_LABEL[kind] || kind}（{items.length} 項）
              </summary>
              {items.map((item) => (
                <ScholarshipCard key={item.name} item={item} />
              ))}
            </details>
          ))}
        </Section>
      )}

      <div className="ncux-card-meta" style={{ marginTop: 12 }}>
        {data.excluded_count > 0 && `另外有 ${data.excluded_count} 項因為學制、年級、系所或成績不符合，沒有列出。`}
        大部分獎學金在
        <a href={data.apply_url} target="_blank" rel="noreferrer">
          獎助學金暨工讀管理系統
        </a>
        線上申請（Portal → 學生服務 → 生活助學服務）。
      </div>
      <div className="ncux-card-meta">
        ※ 依 iNCU 成績跟學校網站上的獎學金辦法自動比對，僅供參考，實際資格以各辦法與承辦單位公告為準。
      </div>
    </div>
  );
}
