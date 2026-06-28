"use client";

import { useEffect, useRef, useState } from "react";
import { Download, Loader2, Send, Sparkles } from "lucide-react";
import clsx from "clsx";

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
  onSend: (message: string) => Promise<{
    answer: string;
    sources?: any[];
    chart?: any;
    route?: string;
    model?: string;
    sql?: string | null;
    rows?: Record<string, any>[];
  }>;
  onStream?: (message: string) => Promise<Response>;
  streamMeta?: { route?: string; model?: string };
  initialMessages?: Message[];
  resetKey?: string;
  onMessagesChange?: (messages: Message[]) => void;
  placeholder?: string;
  emptyTitle?: string;
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
  renderExtra,
}: Props) {
  const [messages, setMessages] = useState<Message[]>(initialMessages);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const streamFrameRef = useRef<number | null>(null);
  const pendingStreamContentRef = useRef("");

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
    textarea.style.height = `${Math.min(textarea.scrollHeight, 160)}px`;
  }, [input]);

  useEffect(() => {
    return () => {
      if (streamFrameRef.current !== null) {
        window.cancelAnimationFrame(streamFrameRef.current);
      }
    };
  }, []);

  const send = async () => {
    const text = input.trim();
    if (!text || loading) return;

    setInput("");
    setLoading(true);

    const appendFinalAnswer = (res: {
      answer: string;
      sources?: any[];
      chart?: any;
      route?: string;
      model?: string;
      sql?: string | null;
      rows?: Record<string, any>[];
    }) => {
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

        const res = await onStream(text);
        const reader = res.body?.getReader();
        if (!reader) throw new Error("No response stream returned.");

        const decoder = new TextDecoder();
        let content = "";

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
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
        if (streamFrameRef.current !== null) {
          window.cancelAnimationFrame(streamFrameRef.current);
          streamFrameRef.current = null;
        }
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
      const res = await onSend(text);
      appendFinalAnswer(res);
    } catch (error) {
      if (onStream) {
        removeStreamDraft();
        try {
          const res = await onSend(text);
          appendFinalAnswer(res);
          return;
        } catch {
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
      setLoading(false);
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
          <div className="soft-fade-in mx-auto mt-16 max-w-md text-center">
            <div className="mx-auto mb-4 grid h-12 w-12 place-items-center rounded-md border border-cyan-300/20 bg-cyan-300/10 text-cyan-200 shadow-lg shadow-cyan-950/25">
              <Sparkles size={20} />
            </div>
            <div className="mb-2 text-sm font-medium text-slate-200">{emptyTitle}</div>
            <p className="text-sm leading-6 text-slate-500">{placeholder}</p>
          </div>
        )}

        <div className="space-y-4">
          {messages.map((msg, i) => (
            <div key={i} className={clsx("soft-fade-in flex", msg.role === "user" ? "justify-end" : "justify-start")}>
              <div
                className={clsx(
                  "max-w-[min(44rem,92%)] rounded-md border px-4 py-3 text-sm leading-6 shadow-lg transition duration-150",
                  msg.role === "user"
                    ? "border-cyan-300/25 bg-cyan-500/95 text-slate-950 shadow-cyan-950/20"
                    : "app-panel text-slate-100"
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
                  <div className="flex items-center gap-2 text-slate-400">
                    <Loader2 size={15} className="animate-spin text-cyan-300" />
                    <span>Thinking...</span>
                  </div>
                )}

                {msg.sources && msg.sources.length > 0 && (
                  <div className="mt-3 border-t border-slate-700 pt-2 text-xs text-slate-400">
                    Sources: {msg.sources.map((s) => s.source).filter(Boolean).join(", ")}
                  </div>
                )}

                {msg.model && (
                  <div className="mt-2 text-xs text-slate-500">
                    {msg.route || "response"} / {msg.model}
                  </div>
                )}

                {renderExtra?.(msg)}

                {msg.role === "assistant" && msg.content.trim() && (
                  <div className="mt-3 border-t border-slate-700 pt-2">
                    <button
                      onClick={() => downloadMessage(msg, i)}
                      className="inline-flex items-center gap-1.5 rounded px-1.5 py-1 text-xs text-slate-500 transition hover:bg-slate-800 hover:text-cyan-200"
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
              <div className="app-panel flex items-center gap-2 rounded-md px-4 py-3 text-sm text-slate-400">
                <Loader2 size={16} className="animate-spin text-cyan-300" />
                Thinking...
              </div>
            </div>
          )}
        </div>

        <div ref={bottomRef} />
      </div>

      <div className="border-t border-slate-800 bg-slate-950/80 px-4 py-4 sm:px-6">
        <div className="app-panel flex items-end gap-3 rounded-md p-2">
          <textarea
            ref={inputRef}
            className="app-input max-h-40 min-h-12 flex-1 resize-none rounded-md px-4 py-3 text-sm placeholder:text-slate-600"
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
          <button
            onClick={send}
            disabled={loading || !input.trim()}
            className="grid h-12 w-12 shrink-0 place-items-center rounded-md bg-cyan-300 text-slate-950 shadow-lg shadow-cyan-950/20 transition duration-150 hover:-translate-y-0.5 hover:bg-cyan-200 active:translate-y-0 disabled:cursor-not-allowed disabled:bg-slate-800 disabled:text-slate-500 disabled:shadow-none"
            aria-label="Send message"
          >
            <Send size={18} />
          </button>
        </div>
      </div>
    </div>
  );
}
