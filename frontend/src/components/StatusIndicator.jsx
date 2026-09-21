export default function StatusIndicator({ text }) {
  if (!text) return null;
  return (
    <div className="status-indicator">
      <span className="dot" />
      {text}
    </div>
  );
}
