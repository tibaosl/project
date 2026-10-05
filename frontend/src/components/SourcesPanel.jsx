export default function SourcesPanel({ sources }) {
  if (!sources || sources.length === 0) return null;

  return (
    <details className="sources-panel">
      <summary>📚 參考資料來源（{sources.length}）</summary>
      <ul>
        {sources.map((src) => {
          const fileName = src.split(" (")[0];
          // 爬蟲抓的文件放在 data/<來源>/ 底下，路徑的斜線要留著，每一段各自編碼
          const href = `/files/${fileName.split("/").map(encodeURIComponent).join("/")}`;
          return (
            <li key={src}>
              <a href={href} target="_blank" rel="noreferrer">
                📄 {src}
              </a>
            </li>
          );
        })}
      </ul>
    </details>
  );
}
