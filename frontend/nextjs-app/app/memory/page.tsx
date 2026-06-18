"use client";

import { useEffect, useState } from "react";
import { Loader2, Plus, RefreshCw, Trash2 } from "lucide-react";
import { api } from "@/lib/api";

type SessionSummary = {
  session_id: string;
  messages: number;
  last_role?: string;
  last_message?: string;
  updated_at?: string;
};

export default function MemoryPage() {
  const [facts, setFacts] = useState<any[]>([]);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [activeSession, setActiveSession] = useState("");
  const [history, setHistory] = useState<any[]>([]);
  const [newFact, setNewFact] = useState("");
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState("");

  const load = async () => {
    setLoading(true);
    setMessage("");
    try {
      const [f, s] = await Promise.all([api.memoryFacts(), api.memorySessions()]);
      setFacts(f);
      setSessions(s);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Failed to load memory");
    } finally {
      setLoading(false);
    }
  };

  const openSession = async (sessionId: string) => {
    setActiveSession(sessionId);
    setHistory(await api.getHistory(sessionId));
  };

  const addFact = async () => {
    if (!newFact.trim()) return;
    await api.addMemoryFact(newFact.trim());
    setNewFact("");
    await load();
  };

  const deleteFact = async (id: string) => {
    await api.deleteMemoryFact(id);
    await load();
  };

  const clearSession = async (sessionId: string) => {
    await api.clearHistory(sessionId);
    if (activeSession === sessionId) {
      setActiveSession("");
      setHistory([]);
    }
    await load();
  };

  useEffect(() => {
    load();
  }, []);

  return (
    <div className="mx-auto max-w-6xl px-4 py-6 sm:px-6 lg:py-8">
      <div className="mb-6 flex items-center justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold text-white">Memory</h1>
          <p className="text-sm text-slate-500">Saved user context and recent conversation sessions.</p>
        </div>
        <button
          onClick={load}
          className="flex items-center gap-2 rounded-md border border-slate-700 px-3 py-2 text-sm text-slate-300 transition hover:border-slate-500 hover:text-white"
        >
          <RefreshCw size={15} className={loading ? "animate-spin" : ""} />
          Refresh
        </button>
      </div>

      {message && <div className="mb-4 rounded-md border border-rose-400/30 bg-rose-400/10 px-3 py-2 text-sm text-rose-200">{message}</div>}

      <section className="mb-4 rounded-md border border-slate-800 bg-slate-900/60 p-4">
        <h2 className="mb-3 text-sm font-semibold text-slate-200">Things To Remember</h2>
        <div className="mb-3 flex gap-2">
          <input
            value={newFact}
            onChange={(e) => setNewFact(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") addFact();
            }}
            className="min-w-0 flex-1 rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 outline-none focus:border-cyan-400"
            placeholder="Example: I prefer concise technical explanations."
          />
          <button
            onClick={addFact}
            disabled={!newFact.trim()}
            className="grid h-10 w-10 place-items-center rounded-md bg-cyan-400 text-slate-950 transition hover:bg-cyan-300 disabled:opacity-40"
            aria-label="Add memory"
            title="Add memory"
          >
            <Plus size={17} />
          </button>
        </div>
        <div className="grid gap-2 md:grid-cols-2">
          {facts.length === 0 ? (
            <div className="text-sm text-slate-500">No saved facts yet.</div>
          ) : (
            facts.map((fact) => (
              <div key={fact.id} className="flex items-start gap-3 rounded-md border border-slate-800 bg-slate-950/70 p-3">
                <p className="min-w-0 flex-1 text-sm leading-6 text-slate-300">{fact.content}</p>
                <button
                  onClick={() => deleteFact(fact.id)}
                  className="grid h-8 w-8 shrink-0 place-items-center rounded-md text-slate-500 transition hover:bg-slate-800 hover:text-rose-200"
                  aria-label="Delete memory"
                  title="Delete memory"
                >
                  <Trash2 size={14} />
                </button>
              </div>
            ))
          )}
        </div>
      </section>

      <div className="grid gap-4 lg:grid-cols-[420px_1fr]">
        <section className="rounded-md border border-slate-800 bg-slate-900/60">
          <div className="border-b border-slate-800 px-4 py-3 text-sm font-semibold text-slate-200">Sessions</div>
          {loading ? (
            <div className="grid h-64 place-items-center text-slate-500"><Loader2 className="animate-spin" size={22} /></div>
          ) : sessions.length === 0 ? (
            <div className="px-4 py-12 text-center text-sm text-slate-500">No sessions stored.</div>
          ) : (
            <div className="divide-y divide-slate-800">
              {sessions.map((session) => (
                <button
                  key={session.session_id}
                  onClick={() => openSession(session.session_id)}
                  className={`w-full px-4 py-3 text-left transition hover:bg-slate-800/70 ${
                    activeSession === session.session_id ? "bg-slate-800/80" : ""
                  }`}
                >
                  <div className="truncate text-sm font-medium text-slate-100">{session.session_id}</div>
                  <div className="mt-1 text-xs text-slate-500">{session.messages} messages</div>
                  <div className="mt-1 truncate text-xs text-slate-400">{session.last_message}</div>
                </button>
              ))}
            </div>
          )}
        </section>

        <section className="rounded-md border border-slate-800 bg-slate-900/60">
          <div className="flex items-center justify-between border-b border-slate-800 px-4 py-3">
            <h2 className="text-sm font-semibold text-slate-200">Conversation</h2>
            {activeSession && (
              <button
                onClick={() => clearSession(activeSession)}
                className="grid h-9 w-9 place-items-center rounded-md border border-slate-700 text-slate-400 transition hover:border-rose-400 hover:text-rose-200"
                aria-label="Clear session"
                title="Clear session"
              >
                <Trash2 size={15} />
              </button>
            )}
          </div>
          <div className="max-h-[34rem] overflow-auto p-4">
            {!activeSession ? (
              <div className="rounded-md border border-dashed border-slate-700 px-4 py-16 text-center text-sm text-slate-500">
                Select a session to view its messages.
              </div>
            ) : (
              <div className="space-y-3">
                {history.map((msg, index) => (
                  <div key={index} className="rounded-md border border-slate-800 bg-slate-950/70 p-3">
                    <div className="mb-1 text-xs uppercase tracking-wide text-slate-600">{msg.role}</div>
                    <p className="whitespace-pre-wrap text-sm leading-6 text-slate-300">{msg.content}</p>
                  </div>
                ))}
              </div>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}
