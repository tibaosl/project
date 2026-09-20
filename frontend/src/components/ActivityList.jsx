import { ActivityMatchCard } from "./ActivityCard";

/** 對應 kind === "activity_recommendations" / "activity_tag_search"。 */
export default function ActivityList({ data }) {
  const recommendations = data.recommendations || {};
  const exhaustedByTag = data.exhausted_by_tag || {};
  const entries = Object.entries(recommendations);
  const anyFound = entries.some(([, items]) => items && items.length > 0);

  if (!anyFound) {
    return <div className="ncux-card-meta">目前開放報名中的活動裡沒有找到符合的場次，之後可以再查一次。</div>;
  }

  return (
    <div>
      {entries.map(([name, items]) => {
        if (!items || items.length === 0) return null;
        const tagExhausted = exhaustedByTag[name] ?? true;
        return (
          <div key={name} style={{ marginBottom: 12 }}>
            <div style={{ fontWeight: 700, marginBottom: 6, fontSize: 13.5 }}>
              {tagExhausted
                ? `【${name}】共找到 ${items.length} 個符合的場次`
                : `【${name}】目前顯示 ${items.length} 個符合的場次（可能還有更多）`}
            </div>
            {items.map((item, i) => (
              <ActivityMatchCard item={item} key={item.session_id || i} />
            ))}
          </div>
        );
      })}
      {data.has_more && (
        <div className="ncux-card-meta">還有更多符合的活動——回覆「繼續」再看幾個，或「全部列出」看完整清單。</div>
      )}
    </div>
  );
}
