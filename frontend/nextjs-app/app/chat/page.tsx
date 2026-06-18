"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { MessageSquareText, Plus, Trash2 } from "lucide-react";
import ChatWindow, { type Message } from "@/components/chat/ChatWindow";
import { api } from "@/lib/api";
import clsx from "clsx";

type Conversation = {
  id: string;
  title: string;
  messages: Message[];
  createdAt: number;
  updatedAt: number;
  messageCount?: number;
};

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
  const [mounted, setMounted] = useState(false);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState("");

  useEffect(() => {
    setMounted(true);
    let cancelled = false;

    const load = async () => {
      try {
        const saved = await api.chatConversations();
        if (cancelled) return;
        if (Array.isArray(saved) && saved.length > 0) {
          const list = saved.map((item: any) => ({
            id: item.id,
            title: item.title,
            messages: [],
            createdAt: item.createdAt,
            updatedAt: item.updatedAt,
            messageCount: item.messages,
          }));
          setConversations(list);
          setActiveId(list[0].id);
          const full = await api.getChatConversation(list[0].id);
          if (!cancelled) {
            setConversations((current) =>
              current.map((conversation) =>
                conversation.id === full.id ? { ...conversation, messages: full.messages || [] } : conversation
              )
            );
          }
          return;
        }

        const first = await api.createChatConversation();
        if (!cancelled) {
          setConversations([{ ...first, messages: first.messages || [] }]);
          setActiveId(first.id);
        }
      } catch {
        const first = createLocalConversation();
        if (!cancelled) {
          setConversations([first]);
          setActiveId(first.id);
        }
      }
    };

    load();
    return () => {
      cancelled = true;
    };
  }, []);

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
    }
  };

  const handleSend = async (message: string) => api.generalChat(message, activeId);
  const handleStream = async (message: string) => api.generalChatStream(message, activeId);

  if (!mounted) {
    return (
      <div className="flex h-[calc(100vh-88px)] min-h-[620px] flex-col lg:h-screen lg:min-h-0 lg:flex-row">
        <aside className="border-b border-slate-800 bg-slate-950/70 p-3 lg:w-80 lg:shrink-0 lg:border-b-0 lg:border-r lg:p-4">
          <div className="mb-3 h-10 rounded-md border border-slate-800 bg-slate-900" />
        </aside>
        <section className="flex min-h-0 flex-1 flex-col">
          <header className="border-b border-slate-800 px-5 py-4">
            <div className="flex items-center gap-3">
              <div className="h-9 w-9 rounded-md bg-slate-900" />
              <div>
                <div className="mb-2 h-5 w-32 rounded bg-slate-900" />
                <div className="h-4 w-64 rounded bg-slate-900" />
              </div>
            </div>
          </header>
          <div className="min-h-0 flex-1" />
        </section>
      </div>
    );
  }

  return (
    <div className="flex h-[calc(100vh-88px)] min-h-[620px] flex-col lg:h-screen lg:min-h-0 lg:flex-row">
      <aside className="border-b border-slate-800/70 bg-slate-950/58 p-3 backdrop-blur-xl lg:w-80 lg:shrink-0 lg:overflow-y-auto lg:border-b-0 lg:border-r lg:p-4">
        <button
          onClick={startNewChat}
          className="mb-3 flex w-full items-center justify-center gap-2 rounded-md border border-cyan-300/20 bg-cyan-300/10 px-3 py-2 text-sm font-medium text-cyan-100 shadow-lg shadow-cyan-950/10 transition duration-150 hover:-translate-y-0.5 hover:border-cyan-300/45 hover:bg-cyan-300/15 active:translate-y-0"
        >
          <Plus size={16} />
          New chat
        </button>

        <div className="flex gap-2 overflow-x-auto pb-1 lg:block lg:space-y-2 lg:overflow-visible lg:pb-0">
          {conversations.map((conversation) => (
            <div
              key={conversation.id}
              className={clsx(
                "group flex min-w-56 items-center gap-3 rounded-md border px-3 py-2 text-left shadow-sm transition duration-150 hover:-translate-y-0.5 active:translate-y-0 lg:w-full lg:min-w-0",
                activeId === conversation.id
                  ? "border-cyan-300/25 bg-cyan-400/14 text-white shadow-cyan-950/20"
                  : "border-slate-800/80 bg-slate-900/58 text-slate-300 hover:border-slate-700 hover:bg-slate-900/85"
              )}
            >
              <button onClick={() => selectConversation(conversation.id)} className="flex min-w-0 flex-1 items-center gap-3 text-left">
                <MessageSquareText size={16} className="shrink-0" />
                <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-medium">{conversation.title}</span>
                <span className={clsx("block text-xs", activeId === conversation.id ? "text-cyan-100/75" : "text-slate-600")}>
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
                  "grid h-8 w-8 shrink-0 place-items-center rounded-md opacity-80 transition hover:bg-slate-950/40 hover:text-rose-200 lg:opacity-0 lg:group-hover:opacity-100",
                  activeId === conversation.id ? "text-cyan-100" : "text-slate-500"
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
      <header className="border-b border-slate-800/70 bg-slate-950/30 px-5 py-4 backdrop-blur">
        <div className="flex items-center gap-3">
          <div className="grid h-9 w-9 place-items-center rounded-md border border-cyan-300/20 bg-cyan-300/10 text-cyan-200">
            <MessageSquareText size={18} />
          </div>
          <div>
            <h1 className="text-lg font-semibold text-white">General Chat</h1>
            <p className="text-sm text-slate-500">Talk to the local assistant without document retrieval.</p>
          </div>
        </div>
      </header>

      <div className="min-h-0 flex-1">
        {activeConversation && (
          <ChatWindow
            onSend={handleSend}
            onStream={handleStream}
            streamMeta={{ route: "general" }}
            initialMessages={activeConversation.messages}
            resetKey={activeConversation.id}
            onMessagesChange={updateActiveMessages}
            placeholder="Ask a general question..."
            emptyTitle="Chat with your local assistant"
          />
        )}
      </div>
      </section>
    </div>
  );
}
