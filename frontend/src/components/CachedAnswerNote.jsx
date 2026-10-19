import Icon from "./Icon";

/** created 是後端給的當地時間（2026-10-18T21:03:11），今天整理的寫「今天」，其他寫「10/18」。 */
export function cachedDayLabel(created, now = new Date()) {
  const date = new Date(created);
  if (Number.isNaN(date.getTime())) return "";
  if (date.toDateString() === now.toDateString()) return "今天";
  return `${date.getMonth() + 1}/${date.getDate()}`;
}

const NOTE_TITLES = {
  cached: "這題之前有人問過，相關文件後來沒有更新，所以直接用當時整理好的回答",
  standard: "這題是常見問題，用對照文件整理好的標準答案回答",
};

/**
 * 法規問答直接用了之前整理好的回答時，回答下面的小字：後端的回答快取（同一個問題被問過、兩次都挑到一樣的文件），
 * 或是常見問題的標準答案（standard）。讓使用者知道這是什麼時候整理的，覺得不對或想要最新的可以按「重新查詢」。
 */
export default function CachedAnswerNote({ created, standard = false, onRefresh, disabled = false }) {
  const day = cachedDayLabel(created);
  let label;
  if (standard) {
    label = day === "今天" ? "標準答案（今天整理）" : day ? `標準答案（${day} 整理）` : "標準答案";
  } else {
    label = day === "今天" ? "今天整理好的回答" : day ? `${day} 整理好的回答` : "之前整理好的回答";
  }
  return (
    <span className="cached-answer-note" title={NOTE_TITLES[standard ? "standard" : "cached"]}>
      <Icon name="clock" />
      <span>{label}</span>
      {onRefresh && (
        <button
          type="button"
          className="answer-action-button"
          onClick={onRefresh}
          disabled={disabled}
          aria-label="重新查詢這則回答"
        >
          <Icon name="refresh" />
          <span>重新查詢</span>
        </button>
      )}
    </span>
  );
}
