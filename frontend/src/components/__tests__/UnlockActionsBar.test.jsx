import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { SessionProvider, useSession } from "../../context/SessionContext";
import UnlockActionsBar from "../UnlockActionsBar";

// UnlockActionsBar 的「已啟用」畫面直接看 session.hasActionAccess（不是
// 自己另外記一個 local state），這裡驗證：只要 context 裡 hasActionAccess
// 是 true，不管是怎麼變成 true 的，都會顯示成功訊息——這是 Chat.jsx 能夠
// 「解鎖成功後多顯示一陣子再收起來」的前提（見 Chat.jsx 的 showUnlockBar）。
function Harness() {
  const { loginWithToken, unlockActionsWithPassword } = useSession();
  return (
    <div>
      <button onClick={() => loginWithToken("oauth_user", "tok-1")}>login-oauth</button>
      <button onClick={() => unlockActionsWithPassword("pw")}>unlock</button>
      <UnlockActionsBar />
    </div>
  );
}

describe("UnlockActionsBar", () => {
  beforeEach(() => {
    sessionStorage.clear();
    vi.restoreAllMocks();
  });

  it("session.hasActionAccess 為 false 時顯示解鎖按鈕，不是成功訊息", async () => {
    const user = userEvent.setup();
    render(
      <SessionProvider>
        <Harness />
      </SessionProvider>
    );

    await user.click(screen.getByText("login-oauth"));

    expect(screen.getByText(/設定 Portal 密碼以啟用/)).toBeInTheDocument();
    expect(screen.queryByText(/已啟用課表/)).not.toBeInTheDocument();
  });

  it("unlockActionsWithPassword 成功、hasActionAccess 變 true 後立刻顯示成功訊息", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue({
      ok: true,
      json: async () => ({ status: "success", token: "upgraded-token" }),
    });

    const user = userEvent.setup();
    render(
      <SessionProvider>
        <Harness />
      </SessionProvider>
    );

    await user.click(screen.getByText("login-oauth"));
    await user.click(screen.getByText("unlock"));

    await waitFor(() => {
      expect(screen.getByText(/已啟用課表\/時數\/報名功能/)).toBeInTheDocument();
    });
  });
});
