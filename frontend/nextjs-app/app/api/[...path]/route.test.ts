import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

describe("server API proxy", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.unstubAllEnvs();
    vi.stubEnv("API_INTERNAL_URL", "http://internal-api:5000");
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllEnvs();
  });

  it("injects the server-only API token and preserves browser cookies", async () => {
    vi.stubEnv("API_AUTH_TOKEN", "server-boundary-secret");
    const upstream = new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: {
        "Content-Type": "application/json",
        "Set-Cookie": "ai_platform_session=opaque; HttpOnly; SameSite=Lax; Path=/",
      },
    });
    const fetchMock = vi.fn().mockResolvedValue(upstream);
    vi.stubGlobal("fetch", fetchMock);
    const { GET } = await import("./route");
    const request = new Request("http://frontend.test/api/auth/me?details=1", {
      headers: {
        Cookie: "ai_platform_session=opaque",
        Origin: "http://frontend.test",
        "X-API-Token": "browser-controlled-value",
        "X-Forwarded-For": "attacker-controlled, 203.0.113.8",
        "X-Forwarded-Host": "attacker.test",
        Forwarded: "for=attacker-controlled",
      },
    });

    const response = await GET(request, {
      params: Promise.resolve({ path: ["auth", "me"] }),
    });

    expect(fetchMock).toHaveBeenCalledOnce();
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const headers = init.headers as Headers;
    expect(url).toBe("http://internal-api:5000/api/auth/me?details=1");
    expect(headers.get("x-api-token")).toBe("server-boundary-secret");
    expect(headers.get("cookie")).toBe("ai_platform_session=opaque");
    expect(headers.get("origin")).toBe("http://frontend.test");
    expect(headers.get("x-forwarded-for")).toBe("203.0.113.8");
    expect(headers.has("x-forwarded-host")).toBe(false);
    expect(headers.has("forwarded")).toBe(false);
    expect(response.headers.get("set-cookie")).toContain("HttpOnly");
  });

  it("strips a browser-supplied API token when the server token is disabled", async () => {
    vi.stubEnv("API_AUTH_TOKEN", "");
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);
    const { GET } = await import("./route");

    await GET(
      new Request("http://frontend.test/api/health", {
        headers: { "X-API-Token": "browser-controlled-value" },
      }),
      { params: Promise.resolve({ path: ["health"] }) },
    );

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect((init.headers as Headers).has("x-api-token")).toBe(false);
  });

  it("uses the native local API when no internal URL is configured", async () => {
    vi.stubEnv("API_INTERNAL_URL", "");
    const fetchMock = vi.fn().mockResolvedValue(new Response("{}"));
    vi.stubGlobal("fetch", fetchMock);
    const { GET } = await import("./route");
    await GET(new Request("http://frontend.test/api/health"), { params: Promise.resolve({ path: ["health"] }) });
    expect(fetchMock.mock.calls[0][0]).toBe("http://127.0.0.1:5000/api/health");
  });

  it("returns a useful JSON error when the API is unreachable", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("fetch failed")));
    const { GET } = await import("./route");
    const response = await GET(new Request("http://frontend.test/api/health"), { params: Promise.resolve({ path: ["health"] }) });
    expect(response.status).toBe(502);
    expect(await response.json()).toEqual({ error: "The backend API is unavailable. Check that it is running and try again." });
  });
});
