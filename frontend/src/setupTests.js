import "@testing-library/jest-dom/vitest";

// jsdom 沒有實作 matchMedia，ThemeContext 用它判斷系統淺色/深色偏好，
// 測試環境裡補一個永遠回傳「不是深色」的假實作，避免直接噴錯。
if (!window.matchMedia) {
  window.matchMedia = (query) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  });
}
