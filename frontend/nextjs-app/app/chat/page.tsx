"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  ArrowUpRight,
  BriefcaseBusiness,
  Brain,
  CheckCircle2,
  CloudOff,
  Database,
  Loader2,
  MessageSquareText,
  Plus,
  RefreshCw,
  Settings,
  Sparkles,
  SquareStack,
  Trash2,
} from "lucide-react";
import clsx from "clsx";
import ChatWindow, { type Message } from "@/components/chat/ChatWindow";
import { api } from "@/lib/api";

type Conversation = {
  id: string;
  title: string;
  messages: Message[];
  createdAt: number;
  updatedAt: number;
  messageCount?: number;
};

type ChatMode = "workspace" | "general";
type ConversationSyncStatus = "syncing" | "synced" | "offline";

const CONVERSATION_LOAD_TIMEOUT_MS = 12_000;

function withConversationTimeout<T>(promise: Promise<T>): Promise<T> {
  let timeoutId: number | undefined;
  const timeout = new Promise<never>((_, reject) => {
    timeoutId = window.setTimeout(() => {
      reject(new Error("The conversation service did not respond in time."));
    }, CONVERSATION_LOAD_TIMEOUT_MS);
  });

  return Promise.race([promise, timeout]).finally(() => {
    if (timeoutId !== undefined) window.clearTimeout(timeoutId);
  });
}

const starters = [
  {
    label: "Search jobs",
    prompt: "Find AI engineer jobs using my saved career profile and criteria",
    description: "Runs the Career Agent job search using saved profile data.",
  },
  {
    label: "List documents",
    prompt: "Show me the documents in my brain",
    description: "Checks the document library and chunk counts.",
  },
  {
    label: "Memory check",
    prompt: "Show my saved memory facts",
    description: "Reads saved facts and recent memory sessions.",
  },
  {
    label: "Model setup",
    prompt: "What models are selected for each task?",
    description: "Shows current model routing from settings.",
  },
];

const toolCards = [
  { href: "/brain", label: "Brain", icon: Brain, detail: "Ask documents and notes" },
  { href: "/documents", label: "Documents", icon: Database, detail: "Preview uploaded files" },
  { href: "/career", label: "Career", icon: BriefcaseBusiness, detail: "Find and score jobs" },
  { href: "/memory", label: "Memory", icon: SquareStack, detail: "Saved facts and sessions" },
  { href: "/analytics", label: "Analytics", icon: Activity, detail: "Usage and latency" },
  { href: "/settings", label: "Settings", icon: Settings, detail: "Model selection" },
];

function createLocalConversation(): Conversation {
  const now = Date.now();
  return {
    id: crypto.randomUUID(),
    title: "New chat",
    messages: [],
    createdAt: now,
    updatedAt: now,
  };
}

function titleFromMessages(messages: Message[]) {
  const firstUser = messages.find((message) => message.role === "user")?.content.trim();
  if (!firstUser) return "New chat";
  return firstUser.length > 42 ? `${firstUser.slice(0, 39)}...` : firstUser;
}

export default function ChatPage() {
  const [initialConversation] = useState(createLocalConversation);
  const [conversations, setConversations] = useState<Conversation[]>(() => [initialConversation]);
  const [activeId, setActiveId] = useState(initialConversation.id);
  const [mode, setMode] = useState<ChatMode>("workspace");
  const [syncStatus, setSyncStatus] = useState<ConversationSyncStatus>("syncing");
  const [syncError, setSyncError] = useState("");
  const syncRunRef = useRef(0);
  const conversationsRef = useRef(conversations);
  const activeIdRef = useRef(activeId);
  const initialRemoteConversationRef = useRef<Promise<any> | null>(null);

  conversationsRef.current = conversations;
  activeIdRef.current = activeId;

  const ensureInitialRemoteConversation = useCallback(() => {
    if (!initialRemoteConversationRef.current) {
      initialRemoteConversationRef.current = api
        .createChatConversation(initialConversation.id, initialConversation.title)
        .catch((error) => {
          initialRemoteConversationRef.current = null;
          throw error;
        });
    }
    return initialRemoteConversationRef.current;
  }, [initialConversation.id, initialConversation.title]);

  const loadRemoteConversations = useCallback(async () => {
    const runId = syncRunRef.current + 1;
    syncRunRef.current = runId;
    setSyncStatus("syncing");
    setSyncError("");

    try {
      const saved = await withConversationTimeout(api.chatConversations());
      if (syncRunRef.current !== runId) return;

      if (Array.isArray(saved) && saved.length > 0) {
        const list = saved.map((item: any) => ({
          id: item.id,
          title: item.title,
          messages: [],
          createdAt: item.createdAt,
          updatedAt: item.updatedAt,
          messageCount: item.messages,
        }));
        const remoteIds = new Set(list.map((conversation: Conversation) => conversation.id));
        const localDrafts = conversationsRef.current.filter(
          (conversation) =>
            !remoteIds.has(conversation.id) &&
            (conversation.id !== initialConversation.id || conversation.messages.length > 0),
        );
        const merged = [...localDrafts, ...list];
        const nextActiveId = merged.some((conversation) => conversation.id === activeIdRef.current)
          ? activeIdRef.current
          : list[0].id;

        conversationsRef.current = merged;
        activeIdRef.current = nextActiveId;
        setConversations(merged);
        setActiveId(nextActiveId);

        const historyId = remoteIds.has(nextActiveId) ? nextActiveId : list[0].id;
        const full = await withConversationTimeout(api.getChatConversation(historyId));
        if (syncRunRef.current !== runId) return;
        setConversations((current) =>
          current.map((conversation) =>
            conversation.id === full.id ? { ...conversation, messages: full.messages || [] } : conversation,
          ),
        );
      } else {
        const created = await withConversationTimeout(ensureInitialRemoteConversation());
        if (syncRunRef.current !== runId) return;
        setConversations((current) =>
          current.map((conversation) =>
            conversation.id === initialConversation.id
              ? {
                  ...conversation,
                  ...created,
                  messages: conversation.messages.length ? conversation.messages : created.messages || [],
                }
              : conversation,
          ),
        );
      }

      setSyncStatus("synced");
    } catch (error) {
      if (syncRunRef.current !== runId) return;
      setSyncStatus("offline");
      setSyncError(error instanceof Error ? error.message : "Conversation sync failed.");
    }
  }, [ensureInitialRemoteConversation, initialConversation.id]);

  useEffect(() => {
    void loadRemoteConversations();
    return () => {
      syncRunRef.current += 1;
    };
  }, [loadRemoteConversations]);

  const activeConversation = useMemo(
    () => conversations.find((conversation) => conversation.id === activeId),
    [activeId, conversations]
  );

  const startNewChat = () => {
    const local = createLocalConversation();
    setConversations((current) => [local, ...current]);
    setActiveId(local.id);
    api.createChatConversation(local.id, local.title).catch(() => {});
  };

  const deleteConversation = (id: string) => {
    api.deleteChatConversation(id).catch(() => {});
    setConversations((current) => {
      const remaining = current.filter((conversation) => conversation.id !== id);
      if (activeId === id) {
        if (remaining[0]) {
          setActiveId(remaining[0].id);
        } else {
          const next = createLocalConversation();
          setActiveId(next.id);
          api.createChatConversation(next.id, next.title).catch(() => {});
          return [next];
        }
      }
      return remaining;
    });
  };

  const updateActiveMessages = useCallback((messages: Message[]) => {
    setConversations((current) =>
      current.map((conversation) =>
        conversation.id === activeId
          ? {
              ...conversation,
              title: titleFromMessages(messages),
              messages,
              updatedAt: Date.now(),
            }
          : conversation
      )
    );
  }, [activeId]);

  useEffect(() => {
    const active = conversations.find((conversation) => conversation.id === activeId);
    if (!active || active.messages.length === 0) return;
    const timeout = window.setTimeout(() => {
      api.saveChatConversation(active.id, active.title, active.messages).catch(() => {});
    }, 400);
    return () => window.clearTimeout(timeout);
  }, [activeId, conversations]);

  const selectConversation = async (id: string) => {
    setActiveId(id);
    const existing = conversations.find((conversation) => conversation.id === id);
    if (existing?.messages.length) return;
    try {
      const full = await api.getChatConversation(id);
      setConversations((current) =>
        current.map((conversation) =>
          conversation.id === id ? { ...conversation, messages: full.messages || [] } : conversation
        )
      );
    } catch {
      // Keep the local placeholder if the remote fetch fails.
    }
  };

  const handleWorkspaceSend = async (message: string, signal?: AbortSignal) => {
    const result = await api.workspaceChat(message, activeId, signal);
    const requestedJobSearch =
      /\b(find|search|look for|scan for)\b[\s\S]{0,80}\bjobs?\b/i.test(message) ||
      /\bjobs?\b[\s\S]{0,80}\b(find|search)\b/i.test(message);
    if (result?.workspace_action === "career_search" || (result?.route === "career" && requestedJobSearch)) {
      window.setTimeout(() => {
        window.location.assign("/career?tab=found");
      }, 700);
    }
    return result;
  };

  const handleGeneralSend = async (message: string, signal?: AbortSignal) =>
    api.generalChat(message, activeId, undefined, signal);
  const handleGeneralStream = async (message: string, signal?: AbortSignal) =>
    api.generalChatStream(message, activeId, undefined, signal);

  return (
    <div className="flex h-[calc(100dvh-176px)] min-h-[560px] flex-col lg:h-dvh lg:min-h-0 lg:flex-row">
      <aside className="border-b border-line-soft bg-panel p-3 lg:w-80 lg:shrink-0 lg:overflow-y-auto lg:border-b-0 lg:border-r lg:p-4">
        <button
          onClick={startNewChat}
          className="mb-3 flex w-full items-center justify-center gap-2 rounded-md border border-brand bg-brand px-3 py-2 text-sm font-semibold text-white transition duration-150 hover:bg-brand-hover"
        >
          <Plus size={16} />
          New chat
        </button>

        <div
          className={clsx(
            "mb-3 rounded-md border px-3 py-2 text-xs",
            syncStatus === "offline"
              ? "border-warning/30 bg-warning/10 text-warning-ink"
              : "border-line-soft bg-panel/70 text-muted",
          )}
          role="status"
          aria-live="polite"
        >
          <div className="flex items-start gap-2">
            {syncStatus === "syncing" ? (
              <Loader2 size={14} className="mt-0.5 shrink-0 animate-spin text-analytic" />
            ) : syncStatus === "offline" ? (
              <CloudOff size={14} className="mt-0.5 shrink-0" />
            ) : (
              <CheckCircle2 size={14} className="mt-0.5 shrink-0 text-success" />
            )}
            <div className="min-w-0 flex-1">
              <div className="font-medium text-ink">
                {syncStatus === "syncing"
                  ? "Syncing conversations"
                  : syncStatus === "offline"
                    ? "Working locally"
                    : "Conversations synced"}
              </div>
              {syncStatus === "offline" && (
                <p className="mt-1 break-words leading-5 text-muted">{syncError}</p>
              )}
            </div>
            {syncStatus === "offline" && (
              <button
                type="button"
                onClick={() => void loadRemoteConversations()}
                className="inline-flex shrink-0 items-center gap-1 rounded border border-warning/30 px-2 py-1 font-medium text-warning-ink transition hover:bg-warning/10"
              >
                <RefreshCw size={12} />
                Retry
              </button>
            )}
          </div>
        </div>

        <div className="mb-4 grid grid-cols-2 rounded-md border border-line-soft bg-panel/70 p-1">
          {(["workspace", "general"] as const).map((item) => (
            <button
              key={item}
              type="button"
              onClick={() => setMode(item)}
              className={clsx(
                "rounded px-3 py-2 text-xs font-medium transition",
                mode === item ? "bg-ink text-white" : "text-muted hover:text-ink"
              )}
            >
              {item === "workspace" ? "Workspace" : "General"}
            </button>
          ))}
        </div>

        <div className="flex gap-2 overflow-x-auto pb-1 lg:block lg:space-y-2 lg:overflow-visible lg:pb-0">
          {conversations.map((conversation) => (
            <div
              key={conversation.id}
              className={clsx(
                "group flex min-w-56 items-center gap-3 rounded-md border px-3 py-2 text-left  transition duration-150   lg:w-full lg:min-w-0",
                activeId === conversation.id
                  ? "border-brand/25 bg-brand/14 text-ink "
                  : "border-line-soft/80 bg-panel/58 text-ink-subtle hover:border-line hover:bg-panel/85"
              )}
            >
              <button onClick={() => selectConversation(conversation.id)} className="flex min-w-0 flex-1 items-center gap-3 text-left">
                <MessageSquareText size={16} className="shrink-0" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium">{conversation.title}</span>
                  <span className={clsx("block text-xs", activeId === conversation.id ? "text-brand-ink/75" : "text-muted-soft")}>
                    {conversation.messages.length || conversation.messageCount || 0} messages
                  </span>
                </span>
              </button>
              <button
                onClick={(event) => {
                  event.stopPropagation();
                  deleteConversation(conversation.id);
                }}
                className={clsx(
                  "grid h-8 w-8 shrink-0 place-items-center rounded-md opacity-80 transition hover:bg-canvas/40 hover:text-danger-ink lg:opacity-0 lg:group-hover:opacity-100",
                  activeId === conversation.id ? "text-brand-ink" : "text-muted"
                )}
                aria-label="Delete conversation"
                title="Delete conversation"
              >
                <Trash2 size={14} />
              </button>
            </div>
          ))}
        </div>
      </aside>

      <section className="flex min-h-0 flex-1 flex-col">
        <header className="border-b border-line-soft bg-panel px-5 py-4">
          <div className="flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
            <div className="flex items-center gap-3">
              <div className="grid h-10 w-10 place-items-center rounded-md border border-brand/20 bg-brand/10 text-brand-ink">
                <Sparkles size={19} />
              </div>
              <div>
                <h1 className="text-lg font-semibold text-ink">AI Workspace</h1>
                <p className="text-sm text-muted">
                  {mode === "workspace"
                    ? "Ask normally, or use tool commands for documents, memory, models, analytics, and career jobs."
                    : "Fast streaming chat with the selected general model."}
                </p>
              </div>
            </div>

            <div className="hidden gap-2 sm:grid sm:grid-cols-3 xl:w-[36rem]">
              {toolCards.slice(0, 3).map(({ href, label, icon: Icon, detail }) => (
                <Link
                  key={href}
                  href={href}
                  className="group rounded-md border border-line-soft bg-panel/55 p-3 transition hover:border-brand/35 hover:bg-panel"
                >
                  <div className="mb-2 flex items-center justify-between gap-2">
                    <span className="inline-flex items-center gap-2 text-sm font-medium text-ink">
                      <Icon size={15} className="text-brand-ink" />
                      {label}
                    </span>
                    <ArrowUpRight size={13} className="text-muted-soft transition group-hover:text-analytic-hover" />
                  </div>
                  <p className="text-xs leading-5 text-muted">{detail}</p>
                </Link>
              ))}
            </div>
          </div>
        </header>

        <div className="min-h-0 flex-1">
          {activeConversation && (
            <ChatWindow
              onSend={mode === "workspace" ? handleWorkspaceSend : handleGeneralSend}
              onStream={mode === "general" ? handleGeneralStream : undefined}
              streamMeta={{ route: mode === "general" ? "general" : "workspace" }}
              initialMessages={activeConversation.messages}
              resetKey={`${activeConversation.id}:${mode}`}
              onMessagesChange={updateActiveMessages}
              placeholder={
                mode === "workspace"
                  ? "Ask about docs, memory, models, analytics, jobs, or a normal question..."
                  : "Ask a general question..."
              }
              emptyTitle={mode === "workspace" ? "Command your AI workspace" : "Chat with your AI assistant"}
              suggestions={mode === "workspace" ? starters : []}
              renderExtra={renderWorkspaceExtra}
            />
          )}
        </div>
      </section>
    </div>
  );
}

function renderWorkspaceExtra(msg: Message) {
  if (msg.role !== "assistant" || !msg.route) return null;
  const hrefByRoute: Record<string, string> = {
    rag: "/brain",
    documents: "/documents",
    memory: "/memory",
    career: "/career?tab=found",
    bi: "/dashboard",
    analytics: "/analytics",
    settings: "/settings",
  };
  const href = hrefByRoute[msg.route];
  if (!href) return null;
  return (
    <Link
      href={href}
      className="mt-3 inline-flex items-center gap-1.5 rounded-md border border-line px-2.5 py-1.5 text-xs text-ink-subtle transition hover:border-brand/50 hover:text-analytic-hover"
    >
      Open {msg.route}
      <ArrowUpRight size={12} />
    </Link>
  );
}
