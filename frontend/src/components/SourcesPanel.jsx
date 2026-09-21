export default function SourcesPanel({ sources }) {
  if (!sources || sources.length === 0) return null;

  return (
    <details className="sources-panel">
      <summary>📚 參考資料來源（{sources.length}）</summary>
      <ul>
        {sources.map((src) => {
          const fileName = src.split(" (")[0];
          return (
            <li key={src}>
              <a href={`/files/${encodeURIComponent(fileName)}`} target="_blank" rel="noreferrer">
                📄 {src}
              </a>
            </li>
          );
        })}
      </ul>
    </details>
  );
}
