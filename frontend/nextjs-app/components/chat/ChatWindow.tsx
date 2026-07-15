"use client";

import { useEffect, useRef, useState } from "react";
import { Download, Loader2, Send, Sparkles, Square } from "lucide-react";
import clsx from "clsx";

export const STREAM_INACTIVITY_TIMEOUT_MS = 30_000;

type AbortReason = "stopped" | "timeout" | "unmount" | null;

type ChatResult = {
  answer: string;
  sources?: any[];
  chart?: any;
  route?: string;
  model?: string;
  sql?: string | null;
  rows?: Record<string, any>[];
};

function abortError() {
  const error = new Error("Request aborted");
  error.name = "AbortError";
  return error;
}

function abortable<T>(promise: Promise<T>, signal: AbortSignal): Promise<T> {
  if (signal.aborted) return Promise.reject(abortError());

  return new Promise<T>((resolve, reject) => {
    const onAbort = () => {
      signal.removeEventListener("abort", onAbort);
      reject(abortError());
    };
    signal.addEventListener("abort", onAbort, { once: true });
    promise.then(
      (value) => {
        signal.removeEventListener("abort", onAbort);
        resolve(value);
      },
      (error) => {
        signal.removeEventListener("abort", onAbort);
        reject(error);
      },
    );
  });
}

export interface Message {
  role: "user" | "assistant";
  content: string;
  sources?: { source: string; score: number }[];
  chart?: object;
  route?: string;
  model?: string;
  sql?: string | null;
  rows?: Record<string, any>[];
}

interface Props {
  onSend: (message: string, signal?: AbortSignal) => Promise<ChatResult>;
  onStream?: (message: string, signal?: AbortSignal) => Promise<Response>;
  streamMeta?: { route?: string; model?: string };
  initialMessages?: Message[];
  resetKey?: string;
  onMessagesChange?: (messages: Message[]) => void;
  placeholder?: string;
  emptyTitle?: string;
  suggestions?: { label: string; prompt: string; description?: string }[];
  renderExtra?: (msg: Message) => React.ReactNode;
}

export default function ChatWindow({
  onSend,
  onStream,
  streamMeta,
  initialMessages = [],
  resetKey,
  onMessagesChange,
  placeholder = "Ask anything...",
  emptyTitle = "Start a conversation",
  suggestions = [],
  renderExtra,
}: Props) {
  const [messages, setMessages] = useState<Message[]>(initialMessages);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const streamFrameRef = useRef<number | null>(null);
  const pendingStreamContentRef = useRef("");
  const activeControllerRef = useRef<AbortController | null>(null);
  const activeReaderRef = useRef<ReadableStreamDefaultReader<Uint8Array> | null>(null);
  const streamInactivityTimerRef = useRef<number | null>(null);
  const abortReasonRef = useRef<AbortReason>(null);
  const mountedRef = useRef(true);

  const clearStreamInactivityTimer = () => {
    if (streamInactivityTimerRef.current !== null) {
      window.clearTimeout(streamInactivityTimerRef.current);
      streamInactivityTimerRef.current = null;
    }
  };

  const cancelStreamFrame = () => {
    if (streamFrameRef.current !== null) {
      window.cancelAnimationFrame(streamFrameRef.current);
      streamFrameRef.current = null;
    }
    pendingStreamContentRef.current = "";
  };

  const cancelActiveReader = () => {
    const reader = activeReaderRef.current;
    activeReaderRef.current = null;
    if (reader) void reader.cancel().catch(() => {});
  };

  const abortActiveRequest = (reason: Exclude<AbortReason, null>) => {
    const controller = activeControllerRef.current;
    if (!controller || controller.signal.aborted) return;
    abortReasonRef.current = reason;
    clearStreamInactivityTimer();
    cancelStreamFrame();
    controller.abort();
    cancelActiveReader();
  };

  const armStreamInactivityTimer = (controller: AbortController) => {
    clearStreamInactivityTimer();
    streamInactivityTimerRef.current = window.setTimeout(() => {
      if (activeControllerRef.current === controller && !controller.signal.aborted) {
        abortActiveRequest("timeout");
      }
    }, STREAM_INACTIVITY_TIMEOUT_MS);
  };

  useEffect(() => {
    setMessages(initialMessages);
    setInput("");
  }, [resetKey]);

  useEffect(() => {
    onMessagesChange?.(messages);
  }, [messages, onMessagesChange]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, loading]);

  useEffect(() => {
    const textarea = inputRef.current;
    if (!textarea) return;
    textarea.style.height = "auto";
    textarea.style.height = input ? `${Math.min(textarea.scrollHeight, 160)}px` : "48px";
  }, [input]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      abortActiveRequest("unmount");
      clearStreamInactivityTimer();
      cancelStreamFrame();
      cancelActiveReader();
    };
  }, []);

  const send = async () => {
    const text = input.trim();
    if (!text || loading) return;

    setInput("");
    setLoading(true);
    const controller = new AbortController();
    activeControllerRef.current = controller;
    abortReasonRef.current = null;

    const appendFinalAnswer = (res: ChatResult) => {
      setMessages((current) => {
        const next = [...current];
        const last = next[next.length - 1];
        const message = {
          role: "assistant" as const,
          content: res.answer || "No answer returned.",
          sources: res.sources,
          chart: res.chart,
          route: res.route,
          model: res.model,
          sql: res.sql,
          rows: res.rows,
        };

        if (last?.role === "assistant" && !last.content.trim()) {
          next[next.length - 1] = message;
          return next;
        }

        return [...next, message];
      });
    };

    const removeStreamDraft = () => {
      setMessages((current) => {
        const next = [...current];
        const last = next[next.length - 1];
        if (last?.role === "assistant" && (!last.content.trim() || last.content.startsWith("[STREAM ERROR]:"))) {
          next.pop();
        }
        return next;
      });
    };

    const appendRequestStatus = (reason: Exclude<AbortReason, null>) => {
      if (reason === "unmount" || !mountedRef.current) return;
      removeStreamDraft();
      setMessages((current) => [
        ...current,
        {
          role: "assistant",
          content:
            reason === "timeout"
              ? "The response timed out after 30 seconds without activity. Please try again."
              : "Response stopped.",
        },
      ]);
    };

    try {
      if (onStream) {
        const updateAssistantDraft = (nextContent: string) => {
          pendingStreamContentRef.current = nextContent;
          if (streamFrameRef.current !== null) return;

          streamFrameRef.current = window.requestAnimationFrame(() => {
            streamFrameRef.current = null;
            const draft = pendingStreamContentRef.current;
            setMessages((current) => {
              const next = [...current];
              const last = next[next.length - 1];
              if (last?.role === "assistant") {
                next[next.length - 1] = { ...last, content: draft };
              }
              return next;
            });
          });
        };

        setMessages((current) => [
          ...current,
          { role: "user", content: text },
          { role: "assistant", content: "", route: streamMeta?.route, model: streamMeta?.model },
        ]);

        armStreamInactivityTimer(controller);
        const res = await abortable(onStream(text, controller.signal), controller.signal);
        armStreamInactivityTimer(controller);
        const reader = res.body?.getReader();
        if (!reader) throw new Error("No response stream returned.");
        activeReaderRef.current = reader;

        const decoder = new TextDecoder();
        let content = "";

        while (true) {
          const { done, value } = await abortable(reader.read(), controller.signal);
          if (done) break;
          armStreamInactivityTimer(controller);
          content += decoder.decode(value, { stream: true });
          if (content.trimStart().startsWith("[STREAM ERROR]:")) {
            throw new Error(content.replace("[STREAM ERROR]:", "").trim() || "Streaming request failed.");
          }
          updateAssistantDraft(content);
        }

        content += decoder.decode();
        if (content.trimStart().startsWith("[STREAM ERROR]:")) {
          throw new Error(content.replace("[STREAM ERROR]:", "").trim() || "Streaming request failed.");
        }
        clearStreamInactivityTimer();
        activeReaderRef.current = null;
        cancelStreamFrame();
        setMessages((current) => {
          const next = [...current];
          const last = next[next.length - 1];
          if (last?.role === "assistant") {
            next[next.length - 1] = {
              ...last,
              content: content.trim() || "No answer returned.",
              model: res.headers.get("X-Model") || last.model,
              route: res.headers.get("X-Route") || last.route,
            };
          }
          return next;
        });
        return;
      }

      setMessages((current) => [...current, { role: "user", content: text }]);
      const res = await abortable(onSend(text, controller.signal), controller.signal);
      appendFinalAnswer(res);
    } catch (error) {
      clearStreamInactivityTimer();
      cancelStreamFrame();
      cancelActiveReader();

      if (controller.signal.aborted) {
        appendRequestStatus(abortReasonRef.current || "stopped");
        return;
      }

      if (onStream) {
        removeStreamDraft();
        try {
          const res = await abortable(onSend(text, controller.signal), controller.signal);
          appendFinalAnswer(res);
          return;
        } catch {
          if (controller.signal.aborted) {
            appendRequestStatus(abortReasonRef.current || "stopped");
            return;
          }
          // Fall through to the visible error below.
        }
      }

      setMessages((current) => [
        ...current,
        {
          role: "assistant",
          content: error instanceof Error ? `Error: ${error.message}` : "Error: request failed.",
        },
      ]);
    } finally {
      clearStreamInactivityTimer();
      cancelStreamFrame();
      cancelActiveReader();
      if (activeControllerRef.current === controller) {
        activeControllerRef.current = null;
        abortReasonRef.current = null;
      }
      if (mountedRef.current) setLoading(false);
    }
  };

  const downloadMessage = (msg: Message, index: number) => {
    const lines = [
      `# ${msg.route || "Assistant"} response`,
      "",
      msg.content,
      "",
      msg.model ? `Model: ${msg.model}` : "",
      msg.sources?.length ? `Sources: ${msg.sources.map((s) => s.source).filter(Boolean).join(", ")}` : "",
    ].filter(Boolean);
    const blob = new Blob([lines.join("\n")], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `assistant-response-${index + 1}.md`;
    link.click();
    URL.revokeObjectURL(url);
  };

  const showingStreamDraft = Boolean(onStream && loading && messages[messages.length - 1]?.role === "assistant");

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-5 sm:px-6">
        {messages.length === 0 && (
          <div className="soft-fade-in mx-auto mt-10 max-w-3xl text-center sm:mt-16">
            <div className="mx-auto mb-4 grid h-12 w-12 place-items-center rounded-md border border-brand/20 bg-brand/10 text-brand-ink">
              <Sparkles size={20} />
            </div>
            <div className="mb-2 text-sm font-medium text-ink">{emptyTitle}</div>
            <p className="text-sm leading-6 text-muted">{placeholder}</p>
            {suggestions.length > 0 && (
              <div className="mt-7 grid gap-2 text-left sm:grid-cols-2">
                {suggestions.map((suggestion) => (
                  <button
                    key={suggestion.prompt}
                    type="button"
                    onClick={() => {
                      setInput(suggestion.prompt);
                      window.setTimeout(() => inputRef.current?.focus(), 0);
                    }}
                    className="rounded-md border border-line-soft bg-panel/62 p-3 text-left transition duration-150 hover:border-brand/35 hover:bg-panel"
                  >
                    <span className="block text-sm font-medium text-ink">{suggestion.label}</span>
                    {suggestion.description && (
                      <span className="mt-1 block text-xs leading-5 text-muted">{suggestion.description}</span>
                    )}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}

        <div className="space-y-4">
          {messages.map((msg, i) => (
            <div key={i} className={clsx("soft-fade-in flex", msg.role === "user" ? "justify-end" : "justify-start")}>
              <div
                className={clsx(
                  "max-w-[min(44rem,92%)] rounded-md border px-4 py-3 text-sm leading-6  transition duration-150",
                  msg.role === "user"
                    ? "border-brand/25 bg-brand/95 text-white "
                    : "app-panel text-ink"
                )}
              >
                {msg.content.trim() ? (
                  <p
                    className={clsx(
                      "whitespace-pre-wrap break-words",
                      showingStreamDraft && i === messages.length - 1 && "streaming-caret"
                    )}
                  >
                    {msg.content}
                  </p>
                ) : (
                  <div className="flex items-center gap-2 text-muted">
                    <Loader2 size={15} className="animate-spin text-analytic" />
                    <span>Thinking...</span>
                  </div>
                )}

                {msg.sources && msg.sources.length > 0 && (
                  <div className="mt-3 border-t border-line pt-2 text-xs text-muted">
                    Sources: {msg.sources.map((s) => s.source).filter(Boolean).join(", ")}
                  </div>
                )}

                {msg.model && (
                  <div className="mt-2 text-xs text-muted">
                    {msg.route || "response"} / {msg.model}
                  </div>
                )}

                {renderExtra?.(msg)}

                {msg.role === "assistant" && msg.content.trim() && (
                  <div className="mt-3 border-t border-line pt-2">
                    <button
                      onClick={() => downloadMessage(msg, i)}
                      className="inline-flex items-center gap-1.5 rounded px-1.5 py-1 text-xs text-muted transition hover:bg-soft hover:text-analytic-hover"
                    >
                      <Download size={13} />
                      Download Markdown
                    </button>
                  </div>
                )}
              </div>
            </div>
          ))}

          {loading && !showingStreamDraft && (
            <div className="flex justify-start">
              <div className="app-panel flex items-center gap-2 rounded-md px-4 py-3 text-sm text-muted">
                <Loader2 size={16} className="animate-spin text-analytic" />
                Thinking...
              </div>
            </div>
          )}
        </div>

        <div ref={bottomRef} />
      </div>

      <div className="border-t border-line-soft bg-soft/55 px-4 py-4 sm:px-6">
        <div className="app-panel flex items-end gap-3 rounded-md p-2">
          <textarea
            ref={inputRef}
            className="app-input max-h-40 min-h-12 flex-1 resize-none rounded-md px-4 py-3 text-sm placeholder:text-muted-soft"
            placeholder={placeholder}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send();
              }
            }}
            rows={1}
          />
          {loading ? (
            <button
              type="button"
              onClick={() => abortActiveRequest("stopped")}
              className="inline-flex h-12 shrink-0 items-center justify-center gap-2 rounded-md border border-danger/30 bg-danger/10 px-3 text-sm font-medium text-danger-ink transition hover:bg-danger/15"
              aria-label="Stop response"
            >
              <Square size={15} fill="currentColor" />
              Stop
            </button>
          ) : (
            <button
              type="button"
              onClick={send}
              disabled={!input.trim()}
              className="grid h-12 w-12 shrink-0 place-items-center rounded-md bg-brand text-white transition duration-150 hover:bg-brand-hover disabled:cursor-not-allowed disabled:bg-soft disabled:text-muted disabled:shadow-none"
              aria-label="Send message"
            >
              <Send size={18} />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
