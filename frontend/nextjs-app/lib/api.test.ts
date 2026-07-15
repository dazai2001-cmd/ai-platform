import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

function captureRequestTimeout() {
  let callback: (() => void) | undefined;
  const handle = 73 as unknown as ReturnType<typeof setTimeout>;
  vi.spyOn(globalThis, "setTimeout").mockImplementation(((handler: TimerHandler) => {
    if (typeof handler === "function") callback = handler as () => void;
    return handle;
  }) as typeof setTimeout);
  return {
    handle,
    expire: () => {
      if (!callback) throw new Error("No request timeout was scheduled");
      callback();
    },
  };
}

describe("API client", () => {
  beforeEach(() => {
    vi.useRealTimers();
    vi.resetModules();
    vi.unstubAllEnvs();
    vi.stubEnv("NEXT_PUBLIC_API_URL", "");
    window.localStorage.clear();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllEnvs();
  });

  it("uses cookie credentials without reading a legacy browser bearer token", async () => {
    window.localStorage.setItem("ai_platform_auth_token", "legacy-session-token");
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ answer: "hello" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { api } = await import("./api");
    await expect(api.chat("  show revenue  ", "conversation-1", "sales")).resolves.toEqual({ answer: "hello" });

    expect(fetchMock).toHaveBeenCalledWith("/api/chat", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        query: "  show revenue  ",
        session_id: "conversation-1",
        dataset: "sales",
      }),
      credentials: "include",
      signal: expect.any(AbortSignal),
    });
  });

  it("surfaces the API error message when a request fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ error: "Dataset was not found" }), {
          status: 404,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );

    const { api } = await import("./api");

    await expect(api.biDatasets()).rejects.toThrow("Dataset was not found");
  });

  it("falls back to the HTTP status when an error response is not JSON", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("upstream unavailable", { status: 503 })));

    const { api } = await import("./api");

    await expect(api.health()).rejects.toThrow("Request failed (503)");
  });

  it("does not set a JSON content type for file uploads", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ source: "report.pdf" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const { api } = await import("./api");
    const file = new File(["pdf"], "report.pdf", { type: "application/pdf" });

    await api.ragUploadPdf(file);

    expect(fetchMock).toHaveBeenCalledOnce();
    const [url, options] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/rag/upload/pdf");
    expect(options.headers).toEqual({});
    expect(options.body).toBeInstanceOf(FormData);
    expect((options.body as FormData).get("file")).toBe(file);
    expect(options.signal).toBeInstanceOf(AbortSignal);
  });

  it("uploads a CV file as multipart form data", async () => {
    const imported = {
      cv_text: "Experienced platform engineer",
      updated_at: 1234,
      filename: "rahul-cv.docx",
      file_type: "docx",
      characters: 29,
      pages: null,
      used_ocr: false,
    };
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(imported), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const { api } = await import("./api");
    const file = new File(["word document"], "rahul-cv.docx", {
      type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    });

    await expect(api.careerImportProfile(file)).resolves.toEqual(imported);

    const [url, options] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/career/profile/import");
    expect(options.method).toBe("POST");
    expect(options.headers).toEqual({});
    expect(options.body).toBeInstanceOf(FormData);
    expect((options.body as FormData).get("file")).toBe(file);
    expect(options.signal).toBeInstanceOf(AbortSignal);
  });

  it("times out when fetch never settles and aborts the underlying request", async () => {
    const timeout = captureRequestTimeout();
    let requestSignal: AbortSignal | undefined;
    vi.stubGlobal("fetch", vi.fn((_url: string, options?: RequestInit) => {
      requestSignal = options?.signal as AbortSignal;
      return new Promise<Response>(() => undefined);
    }));

    const { api } = await import("./api");
    const assertion = expect(api.health()).rejects.toMatchObject({
      name: "TimeoutError",
      message: "Request timed out",
    });

    timeout.expire();
    await assertion;
    expect(requestSignal?.aborted).toBe(true);
  });

  it("times out when JSON body parsing never settles", async () => {
    const timeout = captureRequestTimeout();
    const json = vi.fn(() => new Promise<never>(() => undefined));
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, status: 200, json } as unknown as Response));

    const { api } = await import("./api");
    const assertion = expect(api.health()).rejects.toMatchObject({
      name: "TimeoutError",
      message: "Request timed out",
    });

    await Promise.resolve();
    await Promise.resolve();
    expect(json).toHaveBeenCalledOnce();
    timeout.expire();
    await assertion;
  });

  it("allows callers to abort a JSON chat request", async () => {
    let requestSignal: AbortSignal | undefined;
    vi.stubGlobal("fetch", vi.fn((_url: string, options?: RequestInit) => {
      requestSignal = options?.signal as AbortSignal;
      return new Promise<Response>(() => undefined);
    }));

    const { api } = await import("./api");
    const controller = new AbortController();
    const assertion = expect(
      api.generalChat("hello", undefined, undefined, controller.signal),
    ).rejects.toMatchObject({ name: "AbortError" });

    controller.abort();
    await assertion;
    expect(requestSignal?.aborted).toBe(true);
  });

  it("allows callers to abort a streaming connection", async () => {
    let requestSignal: AbortSignal | undefined;
    vi.stubGlobal("fetch", vi.fn((_url: string, options?: RequestInit) => {
      requestSignal = options?.signal as AbortSignal;
      return new Promise<Response>(() => undefined);
    }));

    const { api } = await import("./api");
    const controller = new AbortController();
    const assertion = expect(
      api.chatStream("hello", undefined, undefined, controller.signal),
    ).rejects.toMatchObject({ name: "AbortError" });

    controller.abort();
    await assertion;
    expect(requestSignal?.aborted).toBe(true);
  });

  it("times out a streaming request that never connects", async () => {
    const timeout = captureRequestTimeout();
    let requestSignal: AbortSignal | undefined;
    vi.stubGlobal("fetch", vi.fn((_url: string, options?: RequestInit) => {
      requestSignal = options?.signal as AbortSignal;
      return new Promise<Response>(() => undefined);
    }));

    const { api } = await import("./api");
    const assertion = expect(api.ragAskStream("question")).rejects.toMatchObject({
      name: "TimeoutError",
      message: "Request timed out",
    });

    timeout.expire();
    await assertion;
    expect(requestSignal?.aborted).toBe(true);
  });

  it("clears the connection timeout after streaming headers arrive", async () => {
    const timeout = captureRequestTimeout();
    const clearTimeoutSpy = vi.spyOn(globalThis, "clearTimeout");
    let requestSignal: AbortSignal | undefined;
    vi.stubGlobal("fetch", vi.fn((_url: string, options?: RequestInit) => {
      requestSignal = options?.signal as AbortSignal;
      return Promise.resolve(new Response("stream body", { status: 200 }));
    }));

    const { api } = await import("./api");
    const controller = new AbortController();
    const response = await api.generalChatStream("hello", undefined, undefined, controller.signal);

    expect(clearTimeoutSpy).toHaveBeenCalledWith(timeout.handle);
    expect(requestSignal?.aborted).toBe(false);
    await expect(response.text()).resolves.toBe("stream body");

    controller.abort();
    expect(requestSignal?.aborted).toBe(true);
  });
});
