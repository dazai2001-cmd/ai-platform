"use client";

import { useEffect, useRef, useState } from "react";
import { Loader2, Send } from "lucide-react";
import clsx from "clsx";

interface Message {
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
  placeholder?: string;
  emptyTitle?: string;
  renderExtra?: (msg: Message) => React.ReactNode;
}

export default function ChatWindow({
  onSend,
  placeholder = "Ask anything...",
  emptyTitle = "Start a conversation",
  renderExtra,
}: Props) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, loading]);

  const send = async () => {
    const text = input.trim();
    if (!text || loading) return;

    setInput("");
    setMessages((current) => [...current, { role: "user", content: text }]);
    setLoading(true);

    try {
      const res = await onSend(text);
      setMessages((current) => [
        ...current,
        {
          role: "assistant",
          content: res.answer || "No answer returned.",
          sources: res.sources,
          chart: res.chart,
          route: res.route,
          model: res.model,
          sql: res.sql,
          rows: res.rows,
        },
      ]);
    } catch (error) {
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

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-5 sm:px-6">
        {messages.length === 0 && (
          <div className="mx-auto mt-16 max-w-md text-center">
            <div className="mb-3 text-sm font-medium text-slate-300">{emptyTitle}</div>
            <p className="text-sm leading-6 text-slate-500">{placeholder}</p>
          </div>
        )}

        <div className="space-y-4">
          {messages.map((msg, i) => (
            <div key={i} className={clsx("flex", msg.role === "user" ? "justify-end" : "justify-start")}>
              <div
                className={clsx(
                  "max-w-[min(44rem,92%)] rounded-md border px-4 py-3 text-sm leading-6 shadow-sm",
                  msg.role === "user"
                    ? "border-indigo-400/40 bg-indigo-500 text-white"
                    : "border-slate-700 bg-slate-900/90 text-slate-100"
                )}
              >
                <p className="whitespace-pre-wrap break-words">{msg.content}</p>

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
              </div>
            </div>
          ))}

          {loading && (
            <div className="flex justify-start">
              <div className="rounded-md border border-slate-700 bg-slate-900 px-4 py-3">
                <Loader2 size={16} className="animate-spin text-cyan-300" />
              </div>
            </div>
          )}
        </div>

        <div ref={bottomRef} />
      </div>

      <div className="border-t border-slate-800 bg-slate-950/80 px-4 py-4 sm:px-6">
        <div className="flex items-end gap-3">
          <textarea
            className="max-h-40 min-h-12 flex-1 resize-none rounded-md border border-slate-700 bg-slate-900 px-4 py-3 text-sm text-slate-100 outline-none placeholder:text-slate-600 focus:border-cyan-400 focus:ring-2 focus:ring-cyan-400/20"
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
            className="grid h-12 w-12 shrink-0 place-items-center rounded-md bg-cyan-400 text-slate-950 transition hover:bg-cyan-300 disabled:cursor-not-allowed disabled:bg-slate-800 disabled:text-slate-500"
            aria-label="Send message"
          >
            <Send size={18} />
          </button>
        </div>
      </div>
    </div>
  );
}
