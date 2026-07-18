import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import ChatWindow, { STREAM_INACTIVITY_TIMEOUT_MS } from "./ChatWindow";

describe("ChatWindow", () => {
  it("uses a suggestion and sends the trimmed prompt", async () => {
    const user = userEvent.setup();
    const onSend = vi.fn().mockResolvedValue({
      answer: "Revenue increased by 12%.",
      route: "bi",
      model: "test-model",
    });

    render(
      <ChatWindow
        onSend={onSend}
        suggestions={[{ label: "Revenue trend", prompt: "  Show the revenue trend  " }]}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Revenue trend" }));
    expect(screen.getByRole("textbox")).toHaveValue("  Show the revenue trend  ");
    await user.keyboard("{Enter}");

    expect(onSend).toHaveBeenCalledWith("Show the revenue trend", expect.any(AbortSignal));
    expect(await screen.findByText("Revenue increased by 12%.")).toBeInTheDocument();
    expect(screen.getByText("bi / test-model")).toBeInTheDocument();
  });

  it("shows a failed request and lets the user retry", async () => {
    const user = userEvent.setup();
    const onSend = vi
      .fn()
      .mockRejectedValueOnce(new Error("Service unavailable"))
      .mockResolvedValueOnce({ answer: "Recovered response" });

    render(<ChatWindow onSend={onSend} placeholder="Ask the assistant" />);

    const input = screen.getByRole("textbox", { name: "Message" });
    await user.type(input, "First attempt{Enter}");
    expect(await screen.findByText("Error: Service unavailable")).toBeInTheDocument();

    await waitFor(() => expect(screen.getByRole("button", { name: "Send message" })).toBeDisabled());
    await user.type(input, "Try again");
    expect(screen.getByRole("button", { name: "Send message" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "Send message" }));

    expect(onSend).toHaveBeenNthCalledWith(2, "Try again", expect.any(AbortSignal));
    expect(await screen.findByText("Recovered response")).toBeInTheDocument();
  });

  it("falls back to the regular request when streaming fails", async () => {
    const user = userEvent.setup();
    const onStream = vi.fn().mockResolvedValue({ body: undefined });
    const onSend = vi.fn().mockResolvedValue({ answer: "Fallback answer", route: "general" });

    render(<ChatWindow onSend={onSend} onStream={onStream} />);

    await user.type(screen.getByRole("textbox"), "Hello{Enter}");

    expect(onStream).toHaveBeenCalledWith("Hello", expect.any(AbortSignal));
    expect(onSend).toHaveBeenCalledWith("Hello", expect.any(AbortSignal));
    expect(await screen.findByText("Fallback answer")).toBeInTheDocument();
    expect(screen.queryByText("No response stream returned.")).not.toBeInTheDocument();
  });

  it("aborts an active stream without falling back when the user stops it", async () => {
    const user = userEvent.setup();
    const onStream = vi.fn((_message: string, _signal?: AbortSignal) => new Promise<Response>(() => {}));
    const onSend = vi.fn().mockResolvedValue({ answer: "Unexpected fallback" });

    render(<ChatWindow onSend={onSend} onStream={onStream} />);

    await user.type(screen.getByRole("textbox"), "Keep going{Enter}");
    const stop = await screen.findByRole("button", { name: "Stop response" });
    const signal = onStream.mock.calls[0][1] as AbortSignal;
    expect(signal.aborted).toBe(false);

    await user.click(stop);

    expect(signal.aborted).toBe(true);
    expect(await screen.findByText("Response stopped.")).toBeInTheDocument();
    expect(onSend).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "Stop response" })).not.toBeInTheDocument();
    expect(screen.queryByText("Thinking...")).not.toBeInTheDocument();
  });

  it("aborts a regular request when the user stops it", async () => {
    const user = userEvent.setup();
    const onSend = vi.fn((_message: string, _signal?: AbortSignal) => new Promise<any>(() => {}));
    render(<ChatWindow onSend={onSend} />);

    await user.type(screen.getByRole("textbox", { name: "Message" }), "Keep calculating{Enter}");
    const stop = await screen.findByRole("button", { name: "Stop response" });
    const signal = onSend.mock.calls[0][1] as AbortSignal;
    expect(signal.aborted).toBe(false);

    await user.click(stop);

    expect(signal.aborted).toBe(true);
    expect(await screen.findByText("Response stopped.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Stop response" })).not.toBeInTheDocument();
  });

  it("times out an inactive stream and releases the loading state", async () => {
    Object.defineProperty(window, "requestAnimationFrame", {
      configurable: true,
      writable: true,
      value: window.requestAnimationFrame,
    });
    Object.defineProperty(window, "cancelAnimationFrame", {
      configurable: true,
      writable: true,
      value: window.cancelAnimationFrame,
    });
    vi.useFakeTimers();
    try {
      const onStream = vi.fn((_message: string, _signal?: AbortSignal) => new Promise<Response>(() => {}));
      const onSend = vi.fn().mockResolvedValue({ answer: "Unexpected fallback" });

      render(<ChatWindow onSend={onSend} onStream={onStream} />);

      const input = screen.getByRole("textbox");
      fireEvent.change(input, { target: { value: "Wait for a stream" } });
      fireEvent.keyDown(input, { key: "Enter", code: "Enter" });
      expect(screen.getByRole("button", { name: "Stop response" })).toBeInTheDocument();

      await act(async () => {
        vi.advanceTimersByTime(STREAM_INACTIVITY_TIMEOUT_MS);
        await Promise.resolve();
      });

      const signal = onStream.mock.calls[0][1] as AbortSignal;
      expect(signal.aborted).toBe(true);
      expect(
        screen.getByText("The response timed out after 30 seconds without activity. Please try again."),
      ).toBeInTheDocument();
      expect(onSend).not.toHaveBeenCalled();
      expect(screen.queryByRole("button", { name: "Stop response" })).not.toBeInTheDocument();
      expect(screen.queryByText("Thinking...")).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });
});
