// 品牌識別標記，統一用在 Login/Chat 的 header——原本各個地方
// 各自重複寫一個包 emoji 🎓 的 span，emoji 字型在不同作業系統/瀏覽器渲染差異
// 很大（有些平台甚至沒有對應字符），改成自畫的 SVG 學士帽圖示，粗細/比例
// 在任何裝置上都一致，也比 emoji 更像一個正式的品牌標記。
export default function Logo({ size = 32 }) {
  return (
    <span className="ncux-logo-badge" style={{ width: size, height: size }} aria-hidden="true">
      <svg viewBox="0 0 24 24" width="58%" height="58%" xmlns="http://www.w3.org/2000/svg">
        <path
          d="M12 2.5 1.5 8 12 13.5 22.5 8 12 2.5Z"
          fill="currentColor"
        />
        <path
          d="M5.5 10.6v4.65c0 .38.17.73.47.96C7.2 17.4 9.6 19 12 19s4.8-1.6 6.03-2.79c.3-.23.47-.58.47-.96V10.6L12 15.1 5.5 10.6Z"
          fill="currentColor"
          opacity="0.6"
        />
        <path d="M21.25 9v6.2" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
      </svg>
    </span>
  );
}
