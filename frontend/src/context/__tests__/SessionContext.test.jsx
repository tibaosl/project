import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, beforeEach } from "vitest";
import { SessionProvider, useSession } from "../SessionContext";

function Probe() {
  const { session, isLoggedIn, login, logout, newConversation } = useSession();
  return (
    <div>
      <div data-testid="logged-in">{String(isLoggedIn)}</div>
      <div data-testid="username">{session?.username ?? ""}</div>
      <div data-testid="thread-id">{session?.threadId ?? ""}</div>
      <button onClick={() => login("test_user", "test_pass")}>login</button>
      <button onClick={logout}>logout</button>
      <button onClick={newConversation}>new-conversation</button>
    </div>
  );
}

describe("SessionContext", () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  it("登入後 isLoggedIn 為 true，且帳號、thread_id 存進 sessionStorage", async () => {
    const user = userEvent.setup();
    render(
      <SessionProvider>
        <Probe />
      </SessionProvider>
    );

    await user.click(screen.getByText("login"));

    expect(screen.getByTestId("logged-in")).toHaveTextContent("true");
    expect(screen.getByTestId("username")).toHaveTextContent("test_user");

    const stored = JSON.parse(sessionStorage.getItem("ncuxplore_session"));
    expect(stored.username).toBe("test_user");
    expect(stored.password).toBe("test_pass");
    expect(stored.threadId).toBeTruthy();
  });

  it("登出後 isLoggedIn 為 false 且清掉 sessionStorage", async () => {
    const user = userEvent.setup();
    render(
      <SessionProvider>
        <Probe />
      </SessionProvider>
    );

    await user.click(screen.getByText("login"));
    await user.click(screen.getByText("logout"));

    expect(screen.getByTestId("logged-in")).toHaveTextContent("false");
    expect(sessionStorage.getItem("ncuxplore_session")).toBeNull();
  });

  it("開新對話會換一個新的 thread_id，但帳密不變", async () => {
    const user = userEvent.setup();
    render(
      <SessionProvider>
        <Probe />
      </SessionProvider>
    );

    await user.click(screen.getByText("login"));
    const firstThreadId = screen.getByTestId("thread-id").textContent;

    await user.click(screen.getByText("new-conversation"));
    const secondThreadId = screen.getByTestId("thread-id").textContent;

    expect(secondThreadId).not.toBe(firstThreadId);
    expect(screen.getByTestId("username")).toHaveTextContent("test_user");
  });
});
