import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import ChatWindow, { STREAM_INACTIVITY_TIMEOUT_MS, STREAM_START_TIMEOUT_MS } from "./ChatWindow";

function useFakeStreamTimers() {
  for (const name of ["requestAnimationFrame", "cancelAnimationFrame"] as const) {
    Object.defineProperty(window, name, {
      configurable: true,
      writable: true,
      value: window[name],
    });
  }
  vi.useFakeTimers();
}

describe("ChatWindow", () => {
  it("shows asynchronously loaded history without publishing an empty replacement", async () => {
    const onMessagesChange = vi.fn();
    const onSend = vi.fn();
    const { rerender } = render(
      <ChatWindow onSend={onSend} initialMessages={[]} resetKey="saved-chat" onMessagesChange={onMessagesChange} />,
    );

    rerender(
      <ChatWindow
        onSend={onSend}
        initialMessages={[{ role: "assistant", content: "Restored answer" }]}
        resetKey="saved-chat"
        onMessagesChange={onMessagesChange}
      />,
    );

    expect(await screen.findByText("Restored answer")).toBeInTheDocument();
    expect(onMessagesChange).not.toHaveBeenCalled();
  });

  it("does not copy the previous conversation into a newly selected conversation", () => {
    const onSend = vi.fn();
    const firstChange = vi.fn();
    const secondChange = vi.fn();
    const { rerender } = render(
      <ChatWindow onSend={onSend} initialMessages={[{ role: "user", content: "First chat" }]} resetKey="first" onMessagesChange={firstChange} />,
    );

    rerender(
      <ChatWindow onSend={onSend} initialMessages={[{ role: "assistant", content: "Second chat" }]} resetKey="second" onMessagesChange={secondChange} />,
    );

    expect(screen.getByText("Second chat")).toBeInTheDocument();
    expect(screen.queryByText("First chat")).not.toBeInTheDocument();
    expect(secondChange).not.toHaveBeenCalled();
  });

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

  it("reports a provider failure after partial output without submitting the prompt again", async () => {
    const user = userEvent.setup();
    const encoder = new TextEncoder();
    const onStream = vi.fn().mockResolvedValue(new Response(new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode("Partial answer"));
        controller.enqueue(encoder.encode("[STREAM ER"));
        controller.enqueue(encoder.encode("ROR]: Provider disconnected"));
        controller.close();
      },
    })));
    const onSend = vi.fn();
    render(<ChatWindow onSend={onSend} onStream={onStream} />);

    await user.type(screen.getByRole("textbox"), "Hello{Enter}");
    expect(await screen.findByText("Error: Provider disconnected")).toBeInTheDocument();
    expect(onSend).not.toHaveBeenCalled();
    expect(screen.queryByText(/\[STREAM ERROR\]/)).not.toBeInTheDocument();
    expect(screen.queryByText("Thinking...")).not.toBeInTheDocument();
  });

  it("falls back once when a provider error arrives before any output", async () => {
    const user = userEvent.setup();
    const onStream = vi.fn().mockResolvedValue(new Response("[STREAM ERROR]: Provider unavailable"));
    const onSend = vi.fn().mockResolvedValue({ answer: "Recovered answer" });
    render(<ChatWindow onSend={onSend} onStream={onStream} />);

    await user.type(screen.getByRole("textbox"), "Hello{Enter}");
    expect(await screen.findByText("Recovered answer")).toBeInTheDocument();
    expect(onSend).toHaveBeenCalledOnce();
    expect(screen.queryByText(/Provider unavailable/)).not.toBeInTheDocument();
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

  it("marks a request as detached when its chat view unmounts", async () => {
    const onSend = vi.fn((_message: string, _signal?: AbortSignal) => new Promise<any>(() => {}));
    const { unmount } = render(<ChatWindow onSend={onSend} />);
    fireEvent.change(screen.getByRole("textbox", { name: "Message" }), { target: { value: "Generate a preview" } });
    fireEvent.keyDown(screen.getByRole("textbox", { name: "Message" }), { key: "Enter" });
    await waitFor(() => expect(onSend).toHaveBeenCalled());
    const signal = onSend.mock.calls[0][1] as AbortSignal;
    unmount();
    expect(signal.aborted).toBe(true);
    expect(signal.reason).toBe("unmount");
  });

  it("accepts a response when the server takes 45 seconds to connect", async () => {
    useFakeStreamTimers();
    try {
      const onStream = vi.fn((_message: string, _signal?: AbortSignal) => new Promise<Response>((resolve) => {
        setTimeout(() => resolve(new Response("Answer after server startup")), 45_000);
      }));
      const onSend = vi.fn();
      render(<ChatWindow onSend={onSend} onStream={onStream} />);

      fireEvent.change(screen.getByRole("textbox"), { target: { value: "What information do you have?" } });
      fireEvent.keyDown(screen.getByRole("textbox"), { key: "Enter", code: "Enter" });

      await act(async () => { await vi.advanceTimersByTimeAsync(45_000); });

      expect(onStream.mock.calls[0][1]?.aborted).toBe(false);
      expect(screen.getByText("Answer after server startup")).toBeInTheDocument();
      expect(onSend).not.toHaveBeenCalled();
      expect(screen.queryByRole("button", { name: "Stop response" })).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("waits for the first answer when headers arrive before a slow model", async () => {
    useFakeStreamTimers();
    try {
      let outputTimer: ReturnType<typeof setTimeout>;
      const onStream = vi.fn((_message: string, _signal?: AbortSignal) => Promise.resolve(new Response(
        new ReadableStream({
          start(controller) {
            outputTimer = setTimeout(() => {
              controller.enqueue(new TextEncoder().encode("Answer after model startup"));
              controller.close();
            }, 45_000);
          },
          cancel() { clearTimeout(outputTimer); },
        }),
      )));
      const onSend = vi.fn();
      render(<ChatWindow onSend={onSend} onStream={onStream} />);

      fireEvent.change(screen.getByRole("textbox"), { target: { value: "Find my note" } });
      fireEvent.keyDown(screen.getByRole("textbox"), { key: "Enter", code: "Enter" });

      await act(async () => { await vi.advanceTimersByTimeAsync(45_000); });

      expect(onStream.mock.calls[0][1]?.aborted).toBe(false);
      expect(screen.getByText("Answer after model startup")).toBeInTheDocument();
      expect(onSend).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  it("bounds server startup and releases the loading state if it never connects", async () => {
    useFakeStreamTimers();
    try {
      const onStream = vi.fn((_message: string, _signal?: AbortSignal) => new Promise<Response>(() => {}));
      const onSend = vi.fn().mockResolvedValue({ answer: "Unexpected fallback" });

      render(<ChatWindow onSend={onSend} onStream={onStream} />);

      const input = screen.getByRole("textbox");
      fireEvent.change(input, { target: { value: "Wait for a stream" } });
      fireEvent.keyDown(input, { key: "Enter", code: "Enter" });
      expect(screen.getByRole("button", { name: "Stop response" })).toBeInTheDocument();

      await act(async () => {
        vi.advanceTimersByTime(STREAM_START_TIMEOUT_MS);
        await Promise.resolve();
      });

      const signal = onStream.mock.calls[0][1] as AbortSignal;
      expect(signal.aborted).toBe(true);
      expect(
        screen.getByText("The server took too long to start a response. Please try again."),
      ).toBeInTheDocument();
      expect(onSend).not.toHaveBeenCalled();
      expect(screen.queryByRole("button", { name: "Stop response" })).not.toBeInTheDocument();
      expect(screen.queryByText("Thinking...")).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("keeps receiving tokens but aborts if an answer then stalls for 30 seconds", async () => {
    useFakeStreamTimers();
    try {
      let output!: ReadableStreamDefaultController<Uint8Array>;
      const onStream = vi.fn((_message: string, _signal?: AbortSignal) => Promise.resolve(new Response(
        new ReadableStream<Uint8Array>({
          start(controller) {
            output = controller;
            controller.enqueue(new TextEncoder().encode("Partial answer"));
          },
        }),
      )));
      const onSend = vi.fn();
      render(<ChatWindow onSend={onSend} onStream={onStream} />);

      fireEvent.change(screen.getByRole("textbox"), { target: { value: "Summarize my document" } });
      fireEvent.keyDown(screen.getByRole("textbox"), { key: "Enter", code: "Enter" });

      await act(async () => { await vi.advanceTimersByTimeAsync(25_000); });
      expect(screen.getByText("Partial answer")).toBeInTheDocument();

      await act(async () => {
        output.enqueue(new TextEncoder().encode(" with more detail"));
        await vi.advanceTimersByTimeAsync(25_000);
      });
      expect(onStream.mock.calls[0][1]?.aborted).toBe(false);
      expect(screen.getByText("Partial answer with more detail")).toBeInTheDocument();

      await act(async () => { await vi.advanceTimersByTimeAsync(STREAM_INACTIVITY_TIMEOUT_MS - 25_000); });
      expect(onStream.mock.calls[0][1]?.aborted).toBe(true);
      expect(screen.getByText("Partial answer with more detail")).toBeInTheDocument();
      expect(screen.getByText("The response timed out after 30 seconds without activity. Please try again.")).toBeInTheDocument();
      expect(onSend).not.toHaveBeenCalled();
      expect(screen.queryByRole("button", { name: "Stop response" })).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("bounds the startup wait even when the server sends only whitespace", async () => {
    useFakeStreamTimers();
    try {
      let outputTimer: ReturnType<typeof setInterval>;
      const onStream = vi.fn((_message: string, _signal?: AbortSignal) => Promise.resolve(new Response(
        new ReadableStream({
          start(controller) {
            outputTimer = setInterval(() => controller.enqueue(new TextEncoder().encode(" ")), 15_000);
          },
          cancel() { clearInterval(outputTimer); },
        }),
      )));
      const onSend = vi.fn();
      render(<ChatWindow onSend={onSend} onStream={onStream} />);

      fireEvent.change(screen.getByRole("textbox"), { target: { value: "Find my document" } });
      fireEvent.keyDown(screen.getByRole("textbox"), { key: "Enter", code: "Enter" });
      await act(async () => { await vi.advanceTimersByTimeAsync(STREAM_START_TIMEOUT_MS); });

      expect(onStream.mock.calls[0][1]?.aborted).toBe(true);
      expect(screen.getByText("The server took too long to start a response. Please try again.")).toBeInTheDocument();
      expect(screen.queryByText("Thinking...")).not.toBeInTheDocument();
      expect(onSend).not.toHaveBeenCalled();
      expect(screen.queryByRole("button", { name: "Stop response" })).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });
});
