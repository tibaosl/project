export default function ConnectionBanner() {
  return (
    <div className="ncux-banner ncux-banner-warn connection-banner">
      ⚠️ 無法連線到後端伺服器，請確認伺服器是否已啟動（可以確認 `python run.py` 那個終端機視窗還在跑）。
    </div>
  );
}
