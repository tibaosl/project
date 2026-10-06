import { useTheme } from "../context/ThemeContext";
import Icon from "./Icon";

const OPTIONS = [
  { mode: "light", icon: "sun", label: "淺色" },
  { mode: "dark", icon: "moon", label: "深色" },
  { mode: "system", icon: "monitor", label: "系統預設" },
];

export default function ThemeToggle() {
  const { mode, setThemeMode } = useTheme();

  return (
    <div className="theme-toggle" role="group" aria-label="外觀主題">
      {OPTIONS.map((opt) => (
        <button
          key={opt.mode}
          type="button"
          className={`theme-toggle-btn${mode === opt.mode ? " active" : ""}`}
          onClick={() => setThemeMode(opt.mode)}
          title={opt.label}
          aria-label={opt.label}
          aria-pressed={mode === opt.mode}
        >
          <Icon name={opt.icon} size={15} />
        </button>
      ))}
    </div>
  );
}
