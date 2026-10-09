import { afterEach, expect, it, vi } from "vitest";

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.resetModules(); });

function json(value: unknown, status = 200) { return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } }); }

it("waits for a local workspace task and exposes its clarification", async () => {
  const fetch = vi.fn().mockResolvedValueOnce(json({ local_run: "run-1" }, 202))
    .mockResolvedValueOnce(json({ id: "run-1", status: "awaiting_input", model: "qwen3:8b", question: { kind: "clarification", question: "Which style?", options: ["Watercolor", "Photorealistic"] } }));
  vi.stubGlobal("fetch", fetch);
  const { api } = await import("./api");
  await expect(api.workspaceChat("Make an image", "chat-1")).resolves.toMatchObject({ answer: "Which style?", needs_clarification: true, run_id: "run-1",
    clarification: { options: ["Watercolor", "Photorealistic"] } });
  expect(fetch.mock.calls[1][0]).toBe("/api/local/runs/run-1");
});

it("posts a popup answer to the same checkpoint and waits for the resumed result", async () => {
  const fetch = vi.fn().mockResolvedValueOnce(json({ id: "run-1", status: "queued" }, 202))
    .mockResolvedValueOnce(json({ id: "run-1", status: "complete", result: { answer: "The task continued." } }));
  vi.stubGlobal("fetch", fetch);
  const { api } = await import("./api");
  await expect(api.resumeLocalTask("run-1", "Watercolor, square")).resolves.toMatchObject({ answer: "The task continued.", run_id: "run-1" });
  expect(fetch.mock.calls[0][0]).toBe("/api/local/runs/run-1/resume");
  expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({ answer: "Watercolor, square" });
  expect(fetch.mock.calls[1][0]).toBe("/api/local/runs/run-1");
});

it("preserves verified specialized-agent metadata after local routing", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(json({ local_run: "run-2" }, 202))
    .mockResolvedValueOnce(json({ id: "run-2", status: "complete", model: "qwen3:8b", result: { answer: "A cited answer.", route: "rag", sources: [{ source: "handbook.md", score: 0.5 }] } })));
  const { api } = await import("./api");
  await expect(api.workspaceChat("When is launch?")).resolves.toMatchObject({ route: "rag", sources: [{ source: "handbook.md", score: 0.5 }] });
});

it("keeps ordinary cloud workspace results without polling", async () => {
  const fetch = vi.fn().mockResolvedValue(json({ answer: "Cloud answer", route: "general" }));
  vi.stubGlobal("fetch", fetch);
  const { api } = await import("./api");
  await expect(api.workspaceChat("Hello")).resolves.toEqual({ answer: "Cloud answer", route: "general" });
  expect(fetch).toHaveBeenCalledTimes(1);
});
