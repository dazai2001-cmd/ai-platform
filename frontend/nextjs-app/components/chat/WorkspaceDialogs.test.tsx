import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import ChatWindow from "./ChatWindow";
import type { LocalCapabilities, LocalRun } from "@/lib/api";

const mocks = vi.hoisted(() => ({
  localRuns: vi.fn(), waitLocalTask: vi.fn(), localMedia: vi.fn(), cancelLocalRun: vi.fn(), deleteLocalMedia: vi.fn(),
  localArtifactUrl: vi.fn((path: string) => path),
}));
vi.mock("@/lib/api", () => ({ api: mocks }));

const caps: LocalCapabilities = {
  enabled: true, checkpoint_ready: true, orchestrator: "qwen3:8b", context_tokens: 4096,
  models: [
    { id: "sd-turbo", kind: "image", ready: true, installed: true, reason: "Ready", description: "", license_url: "https://huggingface.co/stabilityai/sd-turbo" },
    { id: "ltx-video-2b-distilled", kind: "video", ready: false, installed: false, reason: "LTX text encoder weights are not installed.", description: "", license_url: "" },
  ],
};
const question = { kind: "clarification", question: "Which style and size would you like?", fields: [
  { id: "style", label: "Visual style", options: ["Watercolor", "Photorealistic"] },
  { id: "shape", label: "Size or shape", options: ["Square (512 by 512)", "Landscape (640 by 384)"] },
] };
const paused: LocalRun = { id: "task-1", session_id: "chat-1", query: "Make a cat image", status: "awaiting_input", model: "qwen3:8b", model_calls: 0, tool_calls: 0, trace: [], question };

beforeEach(() => {
  Object.values(mocks).forEach((mock) => mock.mockClear());
  mocks.localRuns.mockResolvedValue([]); mocks.localMedia.mockResolvedValue([]); mocks.cancelLocalRun.mockResolvedValue({});
  mocks.waitLocalTask.mockResolvedValue({ answer: "Recovered answer", run_id: "task-1" });
});

it("creates media through the Workspace conversation and keeps its answer in chat", async () => {
  const onSend = vi.fn().mockResolvedValue({ answer: "Your image is queued." });
  render(<ChatWindow onSend={onSend} localCapabilities={caps} sessionId="chat-1" />);
  fireEvent.click(screen.getByRole("button", { name: "Create media" }));
  const dialog = screen.getByRole("dialog", { name: "Create media" });
  fireEvent.change(within(dialog).getByLabelText("Subject and scene"), { target: { value: "A cat by a window" } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Generate image" }));
  await waitFor(() => expect(onSend).toHaveBeenCalledWith(expect.stringContaining("A cat by a window. Style: watercolor. Size: square, 512 by 512."), expect.any(AbortSignal)));
  expect(await screen.findByText("Your image is queued.")).toBeInTheDocument();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

it("keeps unavailable video disabled and shows why inside Workspace", async () => {
  render(<ChatWindow onSend={vi.fn()} localCapabilities={caps} sessionId="chat-1" />);
  fireEvent.click(screen.getByRole("button", { name: "Create media" }));
  fireEvent.change(screen.getByLabelText("Output"), { target: { value: "video" } });
  fireEvent.change(screen.getByLabelText("Subject and scene"), { target: { value: "Ocean waves" } });
  expect(screen.getByRole("button", { name: "Generate video" })).toBeDisabled();
  expect(screen.getByText(/LTX text encoder weights are not installed/)).toBeInTheDocument();
});

it("submits a ready LTX video with the supported shape and fixed frame rate", async () => {
  const readyCaps = { ...caps, models: caps.models?.map((model) => ({ ...model, ready: true, reason: "Ready" })) };
  const onSend = vi.fn().mockResolvedValue({ answer: "Your video is queued." });
  render(<ChatWindow onSend={onSend} localCapabilities={readyCaps} sessionId="chat-1" />);
  fireEvent.click(screen.getByRole("button", { name: "Create media" }));
  fireEvent.change(screen.getByLabelText("Output"), { target: { value: "video" } });
  fireEvent.change(screen.getByLabelText("Subject and scene"), { target: { value: "Ocean waves" } });
  fireEvent.change(screen.getByLabelText("Shape"), { target: { value: "landscape" } });
  fireEvent.change(screen.getByLabelText("Duration (seconds)"), { target: { value: "6" } });
  fireEvent.click(screen.getByRole("button", { name: "Generate video" }));
  await waitFor(() => expect(onSend).toHaveBeenCalledWith(expect.stringContaining("704 by 512. Duration: 6 seconds. Frame rate: 25 fps."), expect.any(AbortSignal)));
  expect(await screen.findByText("Your video is queued.")).toBeInTheDocument();
});

it("opens a clarification popup and resumes the same task only after an explicit answer", async () => {
  const user = userEvent.setup();
  const onSend = vi.fn().mockResolvedValue({ answer: question.question, needs_clarification: true, run_id: paused.id, clarification: question });
  const onResume = vi.fn().mockResolvedValue({ answer: "Continuing your image.", run_id: paused.id });
  render(<ChatWindow onSend={onSend} onResume={onResume} localCapabilities={caps} sessionId="chat-1" />);
  await user.type(screen.getByRole("textbox", { name: "Message" }), "Make a cat image{Enter}");
  const dialog = await screen.findByRole("dialog", { name: "A quick clarification" });
  expect(within(dialog).getByRole("button", { name: "Continue" })).toBeDisabled();
  expect(onResume).not.toHaveBeenCalled();
  await user.click(within(dialog).getByRole("button", { name: "Watercolor" }));
  await user.click(within(dialog).getByRole("button", { name: "Square (512 by 512)" }));
  await user.type(within(dialog).getByRole("textbox", { name: "Clarification answer" }), "Keep the window in the scene");
  expect(onResume).not.toHaveBeenCalled();
  await user.click(within(dialog).getByRole("button", { name: "Continue" }));
  expect(onResume).toHaveBeenCalledTimes(1);
  expect(onResume).toHaveBeenCalledWith("task-1", "Visual style: Watercolor. Size or shape: Square (512 by 512). Keep the window in the scene", expect.any(AbortSignal));
  expect(await screen.findByText("Continuing your image.")).toBeInTheDocument();
  expect(onSend).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole("button", { name: "Answer question" })).not.toBeInTheDocument();
});

it("Answer later leaves a recovered task paused and lets the question be reopened", async () => {
  mocks.localRuns.mockResolvedValue([paused]);
  const onResume = vi.fn();
  render(<ChatWindow onSend={vi.fn()} onResume={onResume} localCapabilities={caps} sessionId="chat-1" />);
  const dialog = await screen.findByRole("dialog");
  fireEvent.click(within(dialog).getByRole("button", { name: "Answer later" }));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(onResume).not.toHaveBeenCalled();
  expect(mocks.cancelLocalRun).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Answer pending question" }));
  expect(screen.getByRole("dialog", { name: "A quick clarification" })).toBeInTheDocument();
});

it.each(["running", "complete"])("recovers an undelivered %s task without starting it again", async (status) => {
  mocks.localRuns.mockResolvedValue([{ ...paused, status, question: null }]);
  mocks.waitLocalTask.mockResolvedValue({ answer: "Recovered answer", run_id: paused.id, route: "rag", sources: [{ source: "handbook.txt", score: 0.5 }] });
  const onSend = vi.fn();
  const onResume = vi.fn();
  const onMessagesChange = vi.fn();
  render(<ChatWindow onSend={onSend} onResume={onResume} localCapabilities={caps} sessionId="chat-1"
    initialMessages={[{ role: "user", content: paused.query }]} onMessagesChange={onMessagesChange} />);

  expect(await screen.findByText("Recovered answer")).toBeInTheDocument();
  expect(mocks.waitLocalTask).toHaveBeenCalledWith(paused.id, expect.any(AbortSignal));
  expect(onMessagesChange.mock.calls.at(-1)?.[0].at(-1)).toMatchObject({ run_id: paused.id, sources: [{ source: "handbook.txt" }] });
  expect(onSend).not.toHaveBeenCalled();
  expect(onResume).not.toHaveBeenCalled();
});

it("does not duplicate a completed task already saved in the conversation", async () => {
  mocks.localRuns.mockResolvedValue([{ ...paused, status: "complete", question: null }]);
  render(<ChatWindow onSend={vi.fn()} onResume={vi.fn()} localCapabilities={caps} sessionId="chat-1"
    initialMessages={[{ role: "user", content: paused.query }, { role: "assistant", content: "Saved answer", run_id: paused.id }]} />);
  await waitFor(() => expect(mocks.localRuns).toHaveBeenCalled());
  expect(mocks.waitLocalTask).not.toHaveBeenCalled();
  expect(screen.getAllByText("Saved answer")).toHaveLength(1);
});

it("detaches a recovered task when leaving the view", async () => {
  mocks.localRuns.mockResolvedValue([{ ...paused, status: "running", question: null }]);
  mocks.waitLocalTask.mockImplementation(() => new Promise(() => {}));
  const { unmount } = render(<ChatWindow onSend={vi.fn()} onResume={vi.fn()} localCapabilities={caps} sessionId="chat-1"
    initialMessages={[{ role: "user", content: paused.query }]} />);
  await waitFor(() => expect(mocks.waitLocalTask).toHaveBeenCalled());
  const signal = mocks.waitLocalTask.mock.calls[0][1] as AbortSignal;
  unmount();
  expect(signal.aborted).toBe(true);
  expect(signal.reason).toBe("unmount");
  expect(mocks.cancelLocalRun).not.toHaveBeenCalled();
});

it("shows Stop feedback for a recovered task", async () => {
  mocks.localRuns.mockResolvedValue([{ ...paused, status: "running", question: null }]);
  mocks.waitLocalTask.mockImplementation((_id: string, signal: AbortSignal) => new Promise((_resolve, reject) => {
    signal.addEventListener("abort", () => reject(new DOMException("Stopped", "AbortError")), { once: true });
  }));
  render(<ChatWindow onSend={vi.fn()} onResume={vi.fn()} localCapabilities={caps} sessionId="chat-1"
    initialMessages={[{ role: "user", content: paused.query }]} />);
  fireEvent.click(await screen.findByRole("button", { name: "Stop response" }));
  expect(await screen.findByText("Response stopped.")).toBeInTheDocument();
  expect(screen.queryByText("Thinking...")).not.toBeInTheDocument();
});

it("does not recover a task that the user cancelled", async () => {
  mocks.localRuns.mockResolvedValue([{ ...paused, status: "cancelled", question: null }]);
  render(<ChatWindow onSend={vi.fn()} onResume={vi.fn()} localCapabilities={caps} sessionId="chat-1"
    initialMessages={[{ role: "user", content: paused.query }, { role: "assistant", content: "Response stopped." }]} />);
  await waitFor(() => expect(mocks.localRuns).toHaveBeenCalled());
  expect(mocks.waitLocalTask).not.toHaveBeenCalled();
  expect(screen.getAllByText("Response stopped.")).toHaveLength(1);
});

it("accepts a free-text reply without forcing suggested choices", async () => {
  mocks.localRuns.mockResolvedValue([paused]);
  const onResume = vi.fn().mockResolvedValue({ answer: "Using your custom style.", run_id: "task-1" });
  render(<ChatWindow onSend={vi.fn()} onResume={onResume} localCapabilities={caps} sessionId="chat-1" />);
  const dialog = await screen.findByRole("dialog");
  fireEvent.change(within(dialog).getByLabelText("Clarification answer"), { target: { value: "Style: abstract expressionism, portrait 384 by 640" } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Continue" }));
  await waitFor(() => expect(onResume).toHaveBeenCalledWith("task-1", "Style: abstract expressionism, portrait 384 by 640", expect.any(AbortSignal)));
});

it("does not show another conversation's question and can open its own from Activity", async () => {
  mocks.localRuns.mockResolvedValue([{ ...paused, session_id: "other-chat" }]);
  render(<ChatWindow onSend={vi.fn()} onResume={vi.fn()} localCapabilities={caps} sessionId="chat-1" />);
  await waitFor(() => expect(mocks.localRuns).toHaveBeenCalled());
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Activity" }));
  expect(await screen.findByText("Tasks will appear here when you ask Workspace to do something.")).toBeInTheDocument();
  expect(screen.queryByText("Make a cat image")).not.toBeInTheDocument();
  mocks.localRuns.mockResolvedValue([paused]);
  fireEvent.click(screen.getByRole("button", { name: "Refresh activity" }));
  const answer = await screen.findByRole("button", { name: "Answer question" });
  fireEvent.click(answer);
  expect(screen.getByRole("dialog", { name: "A quick clarification" })).toBeInTheDocument();
  expect(screen.queryByRole("dialog", { name: "Workspace activity" })).not.toBeInTheDocument();
});

it("hides local controls and does not fetch tasks when local features are disabled", () => {
  render(<ChatWindow onSend={vi.fn()} onResume={vi.fn()} localCapabilities={{ enabled: false }} sessionId="chat-1" />);
  expect(screen.queryByRole("button", { name: "Create media" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Activity" })).not.toBeInTheDocument();
  expect(mocks.localRuns).not.toHaveBeenCalled();
});

it("explains why a Workspace task is waiting behind video generation", async () => {
  mocks.localRuns.mockResolvedValue([{ ...paused, status: "queued", question: null, message: "Waiting for the video preview to finish." }]);
  render(<ChatWindow onSend={vi.fn()} localCapabilities={caps} sessionId="chat-1" />);
  fireEvent.click(screen.getByRole("button", { name: "Activity" }));
  expect(await screen.findByText("Waiting for the video preview to finish.")).toBeInTheDocument();
});

it("cancelling a paused task in Activity removes its pending-question controls", async () => {
  mocks.localRuns.mockResolvedValue([paused]);
  const onResume = vi.fn();
  render(<ChatWindow onSend={vi.fn()} onResume={onResume} localCapabilities={caps} sessionId="chat-1" />);
  const dialog = await screen.findByRole("dialog");
  fireEvent.click(within(dialog).getByRole("button", { name: "Answer later" }));
  fireEvent.click(screen.getByRole("button", { name: "Activity" }));
  const cancel = await screen.findByRole("button", { name: "Cancel task" });
  mocks.localRuns.mockResolvedValue([{ ...paused, status: "cancelled", question: null }]);
  fireEvent.click(cancel);
  await waitFor(() => expect(mocks.cancelLocalRun).toHaveBeenCalledWith("task-1"));
  await screen.findByText("cancelled");
  fireEvent.click(screen.getByRole("button", { name: "Close dialog" }));
  expect(screen.queryByRole("button", { name: "Answer pending question" })).not.toBeInTheDocument();
  expect(onResume).not.toHaveBeenCalled();
});
