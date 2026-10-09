"use client";

import { useEffect, useRef, useState } from "react";
import { Activity, Download, Loader2, MessageCircleQuestion, Send, Sparkles, Square, WandSparkles } from "lucide-react";
import clsx from "clsx";
import { STREAM_INACTIVITY_TIMEOUT_MS, STREAM_START_TIMEOUT_MS } from "@/lib/request-timeouts";
import { api, type LocalCapabilities, type LocalMediaJob, type LocalQuestion, type LocalRun } from "@/lib/api";
import LocalMediaCard from "@/components/chat/LocalMediaCard";
import ClarificationDialog from "@/components/chat/ClarificationDialog";
import WorkspaceMediaDialog from "@/components/chat/WorkspaceMediaDialog";
import WorkspaceActivityDialog from "@/components/chat/WorkspaceActivityDialog";

export { STREAM_INACTIVITY_TIMEOUT_MS, STREAM_START_TIMEOUT_MS } from "@/lib/request-timeouts";

type AbortReason = "stopped" | "startup-timeout" | "timeout" | "unmount" | null;

type ChatResult = {
  answer: string;
  sources?: any[];
  chart?: any;
  route?: string;
  model?: string;
  sql?: string | null;
  rows?: Record<string, any>[];
  media_job?: LocalMediaJob;
  needs_clarification?: boolean;
  run_id?: string;
  clarification?: LocalQuestion | null;
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
  media_job?: LocalMediaJob;
  needs_clarification?: boolean;
  run_id?: string;
  clarification?: LocalQuestion | null;
}

const EMPTY_MESSAGES: Message[] = [];

interface Props {
  onSend: (message: string, signal?: AbortSignal) => Promise<ChatResult>;
  onResume?: (runId: string, answer: string, signal?: AbortSignal) => Promise<ChatResult>;
  localCapabilities?: LocalCapabilities | null;
  sessionId?: string;
  onStream?: (message: string, signal?: AbortSignal) => Promise<Response>;
  streamMeta?: { route?: string; model?: string };
  initialMessages?: Message[];
  resetKey?: string;
  onMessagesChange?: (messages: Message[]) => void;
  disabled?: boolean;
  placeholder?: string;
  emptyTitle?: string;
  suggestions?: { label: string; prompt: string; description?: string }[];
  renderExtra?: (msg: Message) => React.ReactNode;
}

export default function ChatWindow({
  onSend,
  onResume,
  localCapabilities,
  sessionId,
  onStream,
  streamMeta,
  initialMessages = EMPTY_MESSAGES,
  resetKey,
  onMessagesChange,
  disabled = false,
  placeholder = "Ask anything...",
  emptyTitle = "Start a conversation",
  suggestions = [],
  renderExtra,
}: Props) {
  const [messages, setRenderedMessages] = useState<Message[]>(initialMessages);
  const messagesRef = useRef(initialMessages);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [pendingQuestion, setPendingQuestion] = useState<{ runId: string; question: LocalQuestion; query?: string } | null>(null);
  const [questionOpen, setQuestionOpen] = useState(false);
  const [questionError, setQuestionError] = useState("");
  const [toolsView, setToolsView] = useState<"media" | "activity" | null>(null);
  const clarificationVersionRef = useRef(0);
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const streamFrameRef = useRef<number | null>(null);
  const pendingStreamContentRef = useRef("");
  const activeControllerRef = useRef<AbortController | null>(null);
  const activeReaderRef = useRef<ReadableStreamDefaultReader<Uint8Array> | null>(null);
  const streamTimerRef = useRef<number | null>(null);
  const abortReasonRef = useRef<AbortReason>(null);
  const mountedRef = useRef(true);

  const setMessages = (update: Message[] | ((current: Message[]) => Message[])) => {
    if (!mountedRef.current) return;
    const next = typeof update === "function" ? update(messagesRef.current) : update;
    messagesRef.current = next;
    setRenderedMessages(next);
    onMessagesChange?.(next);
  };

  const clearStreamTimer = () => {
    if (streamTimerRef.current !== null) {
      window.clearTimeout(streamTimerRef.current);
      streamTimerRef.current = null;
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
    clearStreamTimer();
    cancelStreamFrame();
    controller.abort();
    cancelActiveReader();
  };

  const armStreamTimer = (controller: AbortController, reason: "startup-timeout" | "timeout") => {
    clearStreamTimer();
    streamTimerRef.current = window.setTimeout(() => {
      if (activeControllerRef.current === controller && !controller.signal.aborted) {
        abortActiveRequest(reason);
      }
    }, reason === "startup-timeout" ? STREAM_START_TIMEOUT_MS : STREAM_INACTIVITY_TIMEOUT_MS);
  };

  useEffect(() => {
    messagesRef.current = initialMessages;
    setRenderedMessages(initialMessages);
  }, [initialMessages, resetKey]);

  useEffect(() => {
    setInput("");
    setPendingQuestion(null);
    setQuestionOpen(false);
    setToolsView(null);
    clarificationVersionRef.current += 1;
  }, [resetKey]);

  useEffect(() => {
    if (!localCapabilities?.enabled || !sessionId || !onResume || disabled) return;
    let active = true;
    const version = clarificationVersionRef.current;
    api.localRuns().then((runs) => {
      if (!active || version !== clarificationVersionRef.current) return;
      const run = runs.find((item) => item.session_id === sessionId && item.status === "awaiting_input" && item.question);
      if (run?.question) {
        setPendingQuestion({ runId: run.id, question: run.question, query: run.query });
        setQuestionOpen(true);
      }
    }).catch(() => {});
    return () => { active = false; };
  }, [localCapabilities?.enabled, sessionId, onResume, disabled, resetKey]);

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
      clearStreamTimer();
      cancelStreamFrame();
      cancelActiveReader();
    };
  }, []);

  const send = async (prompt?: string, resumeRunId?: string) => {
    const text = (prompt ?? input).trim();
    if (!text || loading || activeControllerRef.current || disabled || (resumeRunId && !onResume)) return;

    setInput("");
    setLoading(true);
    setQuestionOpen(false);
    setQuestionError("");
    clarificationVersionRef.current += 1;
    const controller = new AbortController();
    activeControllerRef.current = controller;
    abortReasonRef.current = null;
    let streamHasContent = false;

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
          media_job: res.media_job,
          needs_clarification: res.needs_clarification,
          run_id: res.run_id,
          clarification: res.clarification,
        };

        for (let i = 0; i < next.length; i++) {
          if (next[i].needs_clarification && next[i].run_id === (resumeRunId || res.run_id)) {
            next[i] = { ...next[i], needs_clarification: false };
          }
        }

        if (last?.role === "assistant" && !last.content.trim()) {
          next[next.length - 1] = message;
          return next;
        }

        return [...next, message];
      });
      if (!mountedRef.current) return;
      if (res.needs_clarification && res.run_id && onResume) {
        setPendingQuestion({ runId: res.run_id, question: res.clarification || { kind: "clarification", question: res.answer }, query: text });
        setToolsView(null);
        setQuestionOpen(true);
      } else {
        setPendingQuestion(null);
      }
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
            reason === "startup-timeout"
              ? "The server took too long to start a response. Please try again."
              : reason === "timeout"
                ? "The response timed out after 30 seconds without activity. Please try again."
                : "Response stopped.",
        },
      ]);
    };

    try {
      if (onStream && !resumeRunId) {
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

        armStreamTimer(controller, "startup-timeout");
        const res = await abortable(onStream(text, controller.signal), controller.signal);
        const reader = res.body?.getReader();
        if (!reader) throw new Error("No response stream returned.");
        activeReaderRef.current = reader;

        const decoder = new TextDecoder();
        let content = "";

        while (true) {
          const { done, value } = await abortable(reader.read(), controller.signal);
          if (done) break;
          content += decoder.decode(value, { stream: true });
          const errorOffset = content.indexOf("[STREAM ERROR]:");
          if (errorOffset >= 0) {
            streamHasContent = streamHasContent || Boolean(content.slice(0, errorOffset).trim());
            throw new Error(content.slice(errorOffset + "[STREAM ERROR]:".length).trim() || "Streaming request failed.");
          }
          streamHasContent = streamHasContent || Boolean(content.trim());
          if (content.trim()) armStreamTimer(controller, "timeout");
          updateAssistantDraft(content);
        }

        content += decoder.decode();
        const errorOffset = content.indexOf("[STREAM ERROR]:");
        if (errorOffset >= 0) {
          streamHasContent = streamHasContent || Boolean(content.slice(0, errorOffset).trim());
          throw new Error(content.slice(errorOffset + "[STREAM ERROR]:".length).trim() || "Streaming request failed.");
        }
        clearStreamTimer();
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
      const res = await abortable(resumeRunId ? onResume!(resumeRunId, text, controller.signal) : onSend(text, controller.signal), controller.signal);
      appendFinalAnswer(res);
    } catch (error) {
      clearStreamTimer();
      cancelStreamFrame();
      cancelActiveReader();

      if (controller.signal.aborted) {
        appendRequestStatus(abortReasonRef.current || "stopped");
        return;
      }

      if (onStream) removeStreamDraft();
      if (onStream && !streamHasContent) {
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
      if (resumeRunId && mountedRef.current) {
        setQuestionError(error instanceof Error ? error.message : "Could not continue the task.");
        setQuestionOpen(true);
      }
    } finally {
      clearStreamTimer();
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

  const openRunQuestion = (run: LocalRun) => {
    if (!run.question) return;
    clarificationVersionRef.current += 1;
    setToolsView(null);
    setPendingQuestion({ runId: run.id, question: run.question, query: run.query });
    setQuestionError("");
    setQuestionOpen(true);
  };

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
                {msg.needs_clarification && (msg.run_id && onResume ? <button type="button" disabled={loading || disabled}
                  onClick={() => openRunQuestion({ id: msg.run_id!, question: msg.clarification || { kind: "clarification", question: msg.content } } as LocalRun)}
                  className="mt-3 inline-flex items-center gap-1.5 rounded-md border border-brand/25 bg-brand/10 px-3 py-2 text-xs font-medium text-brand-ink">
                  <MessageCircleQuestion size={14} /> Answer question
                </button> : <p className="mt-2 text-xs text-analytic">Reply with the details to continue this task.</p>)}
                {msg.media_job && <LocalMediaCard initialJob={msg.media_job} />}

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
        {localCapabilities?.enabled && <div className="mb-3 flex flex-wrap items-center gap-2">
          <button type="button" disabled={loading || disabled} onClick={() => setToolsView("media")}
            className="inline-flex items-center gap-1.5 rounded-md border border-line-soft bg-panel px-3 py-1.5 text-xs font-medium text-ink-subtle hover:border-brand/35 disabled:opacity-40"><WandSparkles size={14} /> Create media</button>
          <button type="button" onClick={() => setToolsView("activity")}
            className="inline-flex items-center gap-1.5 rounded-md border border-line-soft bg-panel px-3 py-1.5 text-xs font-medium text-ink-subtle hover:border-brand/35"><Activity size={14} /> Activity</button>
          {pendingQuestion && !questionOpen && !loading && <button type="button" disabled={disabled} onClick={() => { setToolsView(null); setQuestionOpen(true); }}
            className="inline-flex items-center gap-1.5 rounded-md border border-brand/25 bg-brand/10 px-3 py-1.5 text-xs font-medium text-brand-ink"><MessageCircleQuestion size={14} /> Answer pending question</button>}
        </div>}
        <div className="app-panel flex items-end gap-3 rounded-md p-2">
          <textarea
            ref={inputRef}
            className="app-input max-h-40 min-h-12 min-w-0 flex-1 resize-none rounded-md px-4 py-3 text-sm placeholder:text-muted-soft"
            placeholder={placeholder}
            aria-label="Message"
            value={input}
            disabled={disabled}
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
              onClick={() => void send()}
              disabled={disabled || !input.trim()}
              className="grid h-12 w-12 shrink-0 place-items-center rounded-md bg-brand text-white transition duration-150 hover:bg-brand-hover disabled:cursor-not-allowed disabled:bg-soft disabled:text-muted disabled:shadow-none"
              aria-label="Send message"
            >
              <Send size={18} />
            </button>
          )}
        </div>
      </div>
      {questionOpen && pendingQuestion && onResume && !loading && <ClarificationDialog
        key={`${pendingQuestion.runId}:${pendingQuestion.question.question}`}
        question={pendingQuestion.question} query={pendingQuestion.query} error={questionError}
        onClose={() => setQuestionOpen(false)} onAnswer={(answer) => void send(answer, pendingQuestion.runId)} />}
      {toolsView === "media" && localCapabilities?.enabled && <WorkspaceMediaDialog capabilities={localCapabilities}
        onClose={() => setToolsView(null)} onCreate={(prompt) => { setToolsView(null); void send(prompt); }} />}
      {toolsView === "activity" && localCapabilities?.enabled && sessionId && <WorkspaceActivityDialog sessionId={sessionId}
        onClose={() => setToolsView(null)} onQuestion={openRunQuestion} onCancelled={(runId) => {
          if (pendingQuestion?.runId === runId) { setPendingQuestion(null); setQuestionOpen(false); }
          setMessages((current) => current.map((message) => message.run_id === runId && message.needs_clarification
            ? { ...message, needs_clarification: false } : message));
        }} />}
    </div>
  );
}
