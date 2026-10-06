// 介面上的小圖示。原本直接用 emoji（💰📅📍🕒✅⚠️☀️🌙…），各平台的 emoji 字型
// 風格、顏色都不一樣，放在卡片裡很突兀（使用者反映）。改成跟 Logo.jsx、Avatar.jsx
// 同一套的自畫線條 SVG：24×24 格線、1.8 粗細、圓角線頭，顏色跟著文字（currentColor）。
const SHAPES = {
  check: <path d="M5 12.5 10 17.5 19 7.5" />,
  x: <path d="M6.5 6.5 17.5 17.5M17.5 6.5 6.5 17.5" />,
  question: (
    <>
      <path d="M9.1 9.3a3 3 0 1 1 4.3 2.7c-.9.45-1.4 1.1-1.4 2.05v.45" />
      <circle cx="12" cy="18" r="1.05" fill="currentColor" stroke="none" />
    </>
  ),
  "check-circle": (
    <>
      <circle cx="12" cy="12" r="8.75" />
      <path d="M8.2 12.4 10.9 15 15.9 9.5" />
    </>
  ),
  alert: (
    <>
      <path d="M10.3 4.6 2.9 17.4a2 2 0 0 0 1.7 3h14.8a2 2 0 0 0 1.7-3L13.7 4.6a2 2 0 0 0-3.4 0Z" />
      <path d="M12 9.6v4" />
      <circle cx="12" cy="16.9" r="1.05" fill="currentColor" stroke="none" />
    </>
  ),
  clock: (
    <>
      <circle cx="12" cy="12" r="8.75" />
      <path d="M12 7.5V12l3 1.8" />
    </>
  ),
  calendar: (
    <>
      <rect x="3.5" y="5" width="17" height="15.5" rx="2.5" />
      <path d="M3.5 10h17M8 3v4M16 3v4" />
    </>
  ),
  pin: (
    <>
      <path d="M12 21.2c-.3 0-6.6-5.6-6.6-11.2a6.6 6.6 0 0 1 13.2 0c0 5.6-6.3 11.2-6.6 11.2Z" />
      <circle cx="12" cy="10" r="2.4" />
    </>
  ),
  user: (
    <>
      <circle cx="12" cy="8" r="3.7" />
      <path d="M4.8 20.2a7.2 7.2 0 0 1 14.4 0" />
    </>
  ),
  users: (
    <>
      <circle cx="9" cy="8.5" r="3.3" />
      <path d="M2.8 19.8a6.2 6.2 0 0 1 12.4 0M15.3 5.4a3.3 3.3 0 0 1 0 6.3M17.6 14.1a6.2 6.2 0 0 1 3.6 5.7" />
    </>
  ),
  form: (
    <>
      <path d="M9 5H7a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2" />
      <rect x="9" y="3.3" width="6" height="3.5" rx="1" />
      <path d="M8.5 11.5h7M8.5 15.5h4.5" />
    </>
  ),
  medal: (
    <>
      <circle cx="12" cy="9" r="5.5" />
      <path d="M8.6 13.4 7.4 21l4.6-2.6 4.6 2.6-1.2-7.6" />
    </>
  ),
  bulb: (
    <>
      <path d="M9.2 17.6h5.6M10.1 20.8h3.8" />
      <path d="M12 3.2a5.8 5.8 0 0 0-3.4 10.5c.55.4.9 1.05.9 1.75v.1h5v-.1c0-.7.35-1.35.9-1.75A5.8 5.8 0 0 0 12 3.2Z" />
    </>
  ),
  file: (
    <>
      <path d="M14 3H7.5A1.5 1.5 0 0 0 6 4.5v15A1.5 1.5 0 0 0 7.5 21h9a1.5 1.5 0 0 0 1.5-1.5V7Z" />
      <path d="M14 3v4h4M9 12h6M9 15.5h4" />
    </>
  ),
  book: (
    <>
      <path d="M12 6.5C10.6 5.3 8.6 4.6 6.3 4.6H4v13.6h2.3c2.3 0 4.3.7 5.7 1.9 1.4-1.2 3.4-1.9 5.7-1.9H20V4.6h-2.3c-2.3 0-4.3.7-5.7 1.9Z" />
      <path d="M12 6.5v13.6" />
    </>
  ),
  coin: (
    <>
      <circle cx="12" cy="12" r="8.75" />
      <path d="M14.6 9.4c-.4-.95-1.4-1.55-2.6-1.55-1.5 0-2.6.8-2.6 1.95 0 2.6 5.4 1.4 5.4 4.2 0 1.15-1.15 2-2.8 2-1.25 0-2.3-.6-2.7-1.55M12 6.4v1.45M12 16.05v1.55" />
    </>
  ),
  cap: (
    <>
      <path d="M12 4.5 2.5 9.2 12 14l9.5-4.8L12 4.5Z" />
      <path d="M6.4 11.3v4.1c1.45 1.5 3.5 2.4 5.6 2.4s4.15-.9 5.6-2.4v-4.1M21.5 9.2v5.3" />
    </>
  ),
  sun: (
    <>
      <circle cx="12" cy="12" r="3.8" />
      <path d="M12 2.8v2.1M12 19.1v2.1M2.8 12h2.1M19.1 12h2.1M5.5 5.5 7 7M17 17l1.5 1.5M5.5 18.5 7 17M17 7l1.5-1.5" />
    </>
  ),
  moon: <path d="M19.8 14.6A8 8 0 0 1 9.4 4.2a8 8 0 1 0 10.4 10.4Z" />,
  monitor: (
    <>
      <rect x="3" y="4.5" width="18" height="12" rx="2" />
      <path d="M8.5 20h7M12 16.5V20" />
    </>
  ),
  "arrow-right": <path d="M5 12h14M13.5 6.5 19 12l-5.5 5.5" />,
  refresh: (
    <>
      <path d="M19.6 13.2A7.7 7.7 0 1 1 17.8 7" />
      <path d="M19.3 3.6v4.1h-4.1" />
    </>
  ),
};

export const ICON_NAMES = Object.keys(SHAPES);

/** name 是上面 SHAPES 的名稱；size 預設跟著字級（1em）。tone 給狀態色：ok、warn、danger。 */
export default function Icon({ name, size = "1em", tone, className = "" }) {
  const classes = ["ncux-icon", tone && `ncux-icon-${tone}`, className].filter(Boolean).join(" ");
  return (
    <svg
      className={classes}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {SHAPES[name]}
    </svg>
  );
}

/** 卡片裡「圖示＋一行說明」：圖示固定在第一行，文字換行時對齊文字，不會繞到圖示底下。 */
export function MetaLine({ icon, children, className = "" }) {
  return (
    <div className={`ncux-card-meta ncux-meta-line ${className}`.trim()}>
      <Icon name={icon} />
      <span>{children}</span>
    </div>
  );
}
