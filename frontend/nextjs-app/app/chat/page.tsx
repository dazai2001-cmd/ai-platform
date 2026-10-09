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
import { api, type LocalCapabilities } from "@/lib/api";
import { conversationTimestamp, createLocalConversation, useChatState, type ChatMode, type Conversation } from "@/lib/chat-state";
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

function titleFromMessages(messages: Message[]) {
  const firstUser = messages.find((message) => message.role === "user")?.content.trim();
  if (!firstUser) return "New chat";
  return firstUser.length > 42 ? `${firstUser.slice(0, 39)}...` : firstUser;
}

export default function ChatPage() {
  const { state, updateState, ready, saving, saveError, flushConversations, waitForSave } = useChatState();
  const { conversations, activeId } = state.workspace;
  const [localCapabilities, setLocalCapabilities] = useState<LocalCapabilities | null>(null);
  const mode = localCapabilities?.enabled === false ? state.workspace.mode : "workspace";
  const [initialConversation] = useState(() => conversations.find((item) => item.id === activeId) || conversations[0]);
  const [loadStatus, setLoadStatus] = useState<ConversationSyncStatus>("syncing");
  const [loadError, setLoadError] = useState("");
  const syncStatus = saveError ? "offline" : saving || conversations.some((item) => item.pendingSync) ? "syncing" : loadStatus;
  const syncError = saveError || loadError;
  const setConversations = useCallback((update: (current: Conversation[]) => Conversation[]) => {
    updateState((current) => ({ ...current, workspace: { ...current.workspace, conversations: update(current.workspace.conversations) } }));
  }, [updateState]);
  const setActiveId = useCallback((id: string) => {
    updateState((current) => ({ ...current, workspace: { ...current.workspace, activeId: id } }));
  }, [updateState]);
  const setMode = (next: ChatMode) => {
    updateState((current) => ({ ...current, workspace: { ...current.workspace, mode: next } }));
  };
  useEffect(() => {
    if (!ready) return;
    let active = true;
    let retry: ReturnType<typeof setTimeout> | undefined;
    const loadMedia = async () => {
      try {
        const caps = await api.localCapabilities();
        if (active) setLocalCapabilities(caps);
      } catch {
        if (active) retry = setTimeout(() => void loadMedia(), 3000);
      }
    };
    api.localCapabilities(false).then((caps) => {
      if (!active) return;
      setLocalCapabilities(caps);
      if (caps.enabled) updateState((current) => current.workspace.mode === "workspace" ? current : {
        ...current, workspace: { ...current.workspace, mode: "workspace" },
      });
      if (caps.enabled) void loadMedia();
    }).catch(() => { if (active) setLocalCapabilities({ enabled: false }); });
    return () => { active = false; clearTimeout(retry); };
  }, [ready, updateState]);
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
    setLoadStatus("syncing");
    setLoadError("");
    void flushConversations();

    try {
      const saved = await withConversationTimeout(api.chatConversations());
      if (syncRunRef.current !== runId) return;

      if (Array.isArray(saved) && saved.length > 0) {
        const list = saved.map((item: any): Conversation => {
          const local = conversationsRef.current.find((conversation) => conversation.id === item.id);
          const updatedAt = conversationTimestamp(item.updatedAt);
          if (local && (local.pendingSync || (local.loaded && local.updatedAt >= updatedAt))) {
            return { ...local, messageCount: item.messages };
          }
          return {
            id: item.id,
            title: item.title,
            messages: local?.messages || [],
            createdAt: conversationTimestamp(item.createdAt),
            updatedAt,
            messageCount: item.messages,
            loaded: false,
          };
        });
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
        setConversations(() => merged);
        setActiveId(nextActiveId);

        const selected = merged.find((conversation) => conversation.id === nextActiveId);
        if (selected && !selected.loaded) {
          const full = await withConversationTimeout(api.getChatConversation(selected.id));
          if (syncRunRef.current !== runId) return;
          setConversations((current) =>
            current.map((conversation) =>
              conversation.id === full.id && !conversation.loaded && !conversation.pendingSync
                ? { ...conversation, messages: full.messages || [], loaded: true } : conversation,
            ),
          );
        }
      } else {
        const created = await withConversationTimeout(ensureInitialRemoteConversation());
        if (syncRunRef.current !== runId) return;
        setConversations((current) =>
          current.map((conversation) =>
            conversation.id === initialConversation.id
              ? {
                  ...conversation,
                  ...created,
                  createdAt: conversationTimestamp(created.createdAt),
                  updatedAt: conversationTimestamp(created.updatedAt),
                  messages: conversation.messages.length ? conversation.messages : created.messages || [],
                  loaded: true,
                }
              : conversation,
          ),
        );
      }

      setLoadStatus("synced");
    } catch (error) {
      if (syncRunRef.current !== runId) return;
      setLoadStatus("offline");
      setLoadError(error instanceof Error ? error.message : "Conversation sync failed.");
    }
  }, [ensureInitialRemoteConversation, initialConversation.id, setConversations, setActiveId, flushConversations]);

  useEffect(() => {
    if (!ready) return;
    void loadRemoteConversations();
    return () => {
      syncRunRef.current += 1;
    };
  }, [loadRemoteConversations, ready]);

  const activeConversation = useMemo(
    () => conversations.find((conversation) => conversation.id === activeId),
    [activeId, conversations]
  );

  const startNewChat = () => {
    const local = { ...createLocalConversation(), pendingSync: true };
    updateState((current) => ({
      ...current,
      workspace: { ...current.workspace, conversations: [local, ...current.workspace.conversations], activeId: local.id },
    }));
  };

  const deleteConversation = (id: string) => {
    updateState((current) => {
      const remaining = current.workspace.conversations.filter((conversation) => conversation.id !== id);
      if (!remaining.length) remaining.push({ ...createLocalConversation(), pendingSync: true });
      return {
        ...current,
        workspace: {
          ...current.workspace,
          conversations: remaining,
          activeId: current.workspace.activeId === id ? remaining[0].id : current.workspace.activeId,
        },
      };
    });
    void waitForSave(id).then(() => api.deleteChatConversation(id)).catch(() => {});
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
              loaded: true,
              pendingSync: true,
            }
          : conversation
      )
    );
  }, [activeId, setConversations]);

  const selectConversation = async (id: string) => {
    setActiveId(id);
    const existing = conversations.find((conversation) => conversation.id === id);
    if (existing?.loaded) return;
    try {
      const full = await api.getChatConversation(id);
      setConversations((current) =>
        current.map((conversation) =>
          conversation.id === id && !conversation.loaded && !conversation.pendingSync
            ? { ...conversation, messages: full.messages || [], loaded: true } : conversation
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
  const handleResume = useCallback((runId: string, answer: string, signal?: AbortSignal) =>
    api.resumeLocalTask(runId, answer, signal), []);

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

        {localCapabilities?.enabled === false && <div className="mb-4 grid grid-cols-2 rounded-md border border-line-soft bg-panel/70 p-1">
          {(["workspace", "general"] as const).map((item) => (
            <button
              key={item}
              type="button"
              onClick={() => setMode(item)}
              aria-pressed={mode === item}
              className={clsx(
                "rounded px-3 py-2 text-xs font-medium transition",
                mode === item ? "bg-ink text-white" : "text-muted hover:text-ink"
              )}
            >
              {item === "workspace" ? "Workspace" : "General"}
            </button>
          ))}
        </div>}

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

      <section className="flex min-h-0 min-w-0 flex-1 flex-col">
        <header className="border-b border-line-soft bg-panel px-5 py-4">
          <div className="flex flex-col gap-4 2xl:flex-row 2xl:items-center 2xl:justify-between">
            <div className="flex min-w-0 flex-1 items-center gap-3">
              <div className="grid h-10 w-10 shrink-0 place-items-center rounded-md border border-brand/20 bg-brand/10 text-brand-ink">
                <Sparkles size={19} />
              </div>
              <div>
                <h1 className="text-lg font-semibold text-ink">{mode === "workspace" ? "AI Workspace" : "General Chat"}</h1>
                <p className="text-sm text-muted">
                  {mode === "workspace"
                    ? localCapabilities?.enabled
                      ? "Chat, work with your documents, or create media. Qwen brings the right tools into this conversation."
                      : "Ask normally, or use tool commands for documents, memory, models, analytics, and career jobs."
                    : "Fast streaming chat with the selected general model."}
                </p>
              </div>
            </div>

            <div className="hidden min-w-0 gap-2 sm:grid sm:grid-cols-3 2xl:w-[36rem] 2xl:shrink-0">
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
              key={`${activeConversation.id}:${mode}`}
              onSend={mode === "workspace" ? handleWorkspaceSend : handleGeneralSend}
              onResume={localCapabilities?.enabled ? handleResume : undefined}
              localCapabilities={localCapabilities}
              sessionId={activeConversation.id}
              disabled={!ready || !activeConversation.loaded}
              onStream={mode === "general" ? handleGeneralStream : undefined}
              streamMeta={{ route: mode === "general" ? "general" : "workspace" }}
              initialMessages={activeConversation.messages}
              resetKey={`${activeConversation.id}:${mode}`}
              onMessagesChange={updateActiveMessages}
              placeholder={
                mode === "workspace"
                  ? localCapabilities?.enabled
                    ? "Ask a question, use your documents, or describe an image or video…"
                    : "Ask about docs, memory, models, analytics, jobs, or a normal question..."
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
