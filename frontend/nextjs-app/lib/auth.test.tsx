import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  me: vi.fn(),
  login: vi.fn(),
  signup: vi.fn(),
  logout: vi.fn(),
  replace: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  api: {
    me: mocks.me,
    login: mocks.login,
    signup: mocks.signup,
    logout: mocks.logout,
  },
}));

vi.mock("next/navigation", () => ({
  usePathname: () => "/dashboard",
  useRouter: () => ({ replace: mocks.replace }),
}));

import { AuthProvider, useAuth } from "./auth";

const user = {
  id: "user-1",
  email: "browser@example.com",
  email_verified: true,
  created_at: 123,
};

function AuthProbe() {
  const auth = useAuth();
  return (
    <div>
      <span>{auth.loading ? "loading" : auth.user?.email || "anonymous"}</span>
      <button type="button" onClick={() => void auth.login("browser@example.com", "password-123")}>
        Sign in
      </button>
    </div>
  );
}

describe("AuthProvider cookie sessions", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.logout.mockResolvedValue({ ok: true });
  });

  it("restores a browser session through /me without a localStorage token", async () => {
    window.localStorage.setItem("ai_platform_auth_token", "legacy-token");
    mocks.me.mockResolvedValue({ user });

    render(
      <AuthProvider>
        <AuthProbe />
      </AuthProvider>,
    );

    expect(await screen.findByText("browser@example.com")).toBeInTheDocument();
    expect(mocks.me).toHaveBeenCalledOnce();
    expect(window.localStorage.getItem("ai_platform_auth_token")).toBeNull();
  });

  it("uses the login response user without persisting its bearer token", async () => {
    window.localStorage.setItem("ai_platform_auth_token", "legacy-token");
    mocks.me.mockRejectedValue(new Error("not signed in"));
    mocks.login.mockResolvedValue({ user, token: "response-token", expires_at: 456 });

    render(
      <AuthProvider>
        <AuthProbe />
      </AuthProvider>,
    );
    expect(await screen.findByText("anonymous")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByText("browser@example.com")).toBeInTheDocument();
    expect(mocks.login).toHaveBeenCalledWith("browser@example.com", "password-123");
    expect(window.localStorage.getItem("ai_platform_auth_token")).toBeNull();
  });
});
