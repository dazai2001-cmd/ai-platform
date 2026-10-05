"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import type { Message } from "@/components/chat/ChatWindow";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";

export type ChatMode = "workspace" | "general";

export type Conversation = {
  id: string;
  title: string;
  messages: Message[];
  createdAt: number;
  updatedAt: number;
  messageCount?: number;
  loaded: boolean;
  pendingSync?: boolean;
};

type ChatSession = { id: string; messages: Message[] };

type ChatState = {
  version: 1;
  workspace: { conversations: Conversation[]; activeId: string; mode: ChatMode };
  brain: ChatSession;
  bi: { activeDataset: string; sessions: Record<string, ChatSession> };
};

type ChatContextValue = {
  state: ChatState;
  updateState: (update: (current: ChatState) => ChatState) => void;
  ready: boolean;
  saving: boolean;
  saveError: string;
  flushConversations: () => Promise<void>;
  waitForSave: (id: string) => Promise<void>;
};

const ChatContext = createContext<ChatContextValue | null>(null);

export function createChatSession(): ChatSession {
  return { id: crypto.randomUUID(), messages: [] };
}

export function createLocalConversation(): Conversation {
  const now = Date.now();
  return { ...createChatSession(), title: "New chat", createdAt: now, updatedAt: now, loaded: true };
}

export function conversationTimestamp(value: number): number {
  return value < 1_000_000_000_000 ? value * 1000 : value;
}

function initialState(): ChatState {
  const conversation = createLocalConversation();
  return {
    version: 1,
    workspace: { conversations: [conversation], activeId: conversation.id, mode: "workspace" },
    brain: createChatSession(),
    bi: { activeDataset: "", sessions: {} },
  };
}

function isSession(value: any): value is ChatSession {
  return typeof value?.id === "string" && Array.isArray(value.messages)
    && value.messages.every((message: any) =>
      (message?.role === "user" || message?.role === "assistant") && typeof message.content === "string",
    );
}

function readState(storageKey: string | null): ChatState {
  const fallback = initialState();
  if (!storageKey) return fallback;
  try {
    const saved = JSON.parse(window.localStorage.getItem(storageKey) || "null");
    if (saved?.version !== 1) return fallback;
    const conversations = Array.isArray(saved.workspace?.conversations)
      ? saved.workspace.conversations.filter((conversation: any) =>
          typeof conversation?.title === "string"
          && Number.isFinite(conversation.createdAt) && Number.isFinite(conversation.updatedAt)
          && isSession(conversation),
        ) as Conversation[]
      : [];
    const restored = conversations.length ? conversations : fallback.workspace.conversations;
    const sessions = Object.fromEntries(
      Object.entries(saved.bi?.sessions || {}).filter(([, session]) => isSession(session)),
    ) as Record<string, ChatSession>;
    return {
      version: 1,
      workspace: {
        conversations: restored,
        activeId: restored.some((conversation) => conversation.id === saved.workspace?.activeId)
          ? saved.workspace.activeId : restored[0].id,
        mode: saved.workspace?.mode === "general" ? "general" : "workspace",
      },
      brain: isSession(saved.brain) ? saved.brain : fallback.brain,
      bi: { activeDataset: typeof saved.bi?.activeDataset === "string" ? saved.bi.activeDataset : "", sessions },
    };
  } catch {
    return fallback;
  }
}

function ScopedChatProvider({ children, storageKey, ready }: { children: React.ReactNode; storageKey: string | null; ready: boolean }) {
  const [state, setState] = useState(() => readState(storageKey));
  const stateRef = useRef(state);
  const activeSavesRef = useRef(new Map<string, Promise<void>>());
  const mountedRef = useRef(true);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");

  const updateState = useCallback((update: (current: ChatState) => ChatState) => {
    const next = update(stateRef.current);
    if (next === stateRef.current) return;
    stateRef.current = next;
    if (mountedRef.current) setState(next);
    if (storageKey) {
      try {
        window.localStorage.setItem(storageKey, JSON.stringify(next));
      } catch {
        // The provider still retains chats across page navigation if storage is unavailable.
      }
    }
  }, [storageKey]);

  const flushConversations = useCallback(async () => {
    const pending = stateRef.current.workspace.conversations.filter((conversation) => conversation.pendingSync);
    if (!pending.length) return;
    if (mountedRef.current) setSaveError("");
    await Promise.all(pending.map((conversation) => {
      const active = activeSavesRef.current.get(conversation.id);
      if (active) return active;

      const save = async () => {
        // Serialize full-history replacements so a slower, older save cannot erase a newer answer.
        while (true) {
          const snapshot = stateRef.current.workspace.conversations.find((item) => item.id === conversation.id);
          if (!snapshot?.pendingSync) return;
          try {
            await api.saveChatConversation(snapshot.id, snapshot.title, snapshot.messages);
            updateState((current) => ({
              ...current,
              workspace: {
                ...current.workspace,
                conversations: current.workspace.conversations.map((item) =>
                  item.id === snapshot.id && item.messages === snapshot.messages
                    ? { ...item, pendingSync: false } : item,
                ),
              },
            }));
          } catch (error) {
            if (mountedRef.current) {
              setSaveError(error instanceof Error ? error.message : "Conversation save failed.");
            }
            return;
          }
        }
      };
      const promise = save().finally(() => {
        activeSavesRef.current.delete(conversation.id);
        if (mountedRef.current) setSaving(activeSavesRef.current.size > 0);
      });
      activeSavesRef.current.set(conversation.id, promise);
      if (mountedRef.current) setSaving(true);
      return promise;
    }));
  }, [updateState]);

  const waitForSave = useCallback(async (id: string) => {
    await activeSavesRef.current.get(id);
  }, []);

  useEffect(() => {
    if (!state.workspace.conversations.some((conversation) => conversation.pendingSync)) return;
    const timer = window.setTimeout(() => void flushConversations(), 400);
    return () => window.clearTimeout(timer);
  }, [state.workspace.conversations, flushConversations]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const value = useMemo(() => ({ state, updateState, ready, saving, saveError, flushConversations, waitForSave }),
    [state, updateState, ready, saving, saveError, flushConversations, waitForSave]);
  return <ChatContext.Provider value={value}>{children}</ChatContext.Provider>;
}

export function ChatStateProvider({ children }: { children: React.ReactNode }) {
  const { user, loading, authRequired } = useAuth();
  const owner = loading ? null : user?.id || (authRequired ? null : "local");
  const storageKey = owner ? `ai_platform_chats:v1:${encodeURIComponent(owner)}` : null;
  return <ScopedChatProvider key={storageKey || "anonymous"} storageKey={storageKey} ready={!loading}>{children}</ScopedChatProvider>;
}

export function useChatState() {
  const context = useContext(ChatContext);
  if (!context) throw new Error("useChatState must be used inside ChatStateProvider");
  return context;
}
