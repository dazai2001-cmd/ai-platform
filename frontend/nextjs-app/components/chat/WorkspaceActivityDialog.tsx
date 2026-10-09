"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2, MessageCircleQuestion, RefreshCw } from "lucide-react";
import { api, type LocalMediaJob, type LocalRun } from "@/lib/api";
import LocalMediaCard from "./LocalMediaCard";
import WorkspaceModal from "./WorkspaceModal";

export default function WorkspaceActivityDialog({ sessionId, onQuestion, onCancelled, onClose }: {
  sessionId: string; onQuestion: (run: LocalRun) => void; onCancelled: (runId: string) => void; onClose: () => void;
}) {
  const [runs, setRuns] = useState<LocalRun[]>([]);
  const [media, setMedia] = useState<LocalMediaJob[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const mounted = useRef(false);
  const refresh = useCallback(async () => {
    try {
      const [tasks, outputs] = await Promise.all([api.localRuns(), api.localMedia()]);
      if (!mounted.current) return;
      setRuns(tasks.filter((run) => run.session_id === sessionId)); setMedia(outputs); setError("");
    } catch (cause) { if (mounted.current) setError(cause instanceof Error ? cause.message : "Could not update Workspace activity."); }
    finally { if (mounted.current) setLoaded(true); }
  }, [sessionId]);
  useEffect(() => { mounted.current = true; void refresh(); return () => { mounted.current = false; }; }, [refresh]);
  useEffect(() => {
    if (!loaded) return;
    const active = runs.some((run) => ["queued", "running"].includes(run.status)) || media.some((job) => ["queued", "running"].includes(job.status) || job.worker_active);
    const timer = window.setTimeout(() => void refresh(), active ? 2000 : 15_000);
    return () => window.clearTimeout(timer);
  }, [loaded, runs, media, refresh]);
  const act = async (operation: () => Promise<unknown>) => {
    setBusy(true); setError("");
    try { await operation(); await refresh(); }
    catch (cause) { if (mounted.current) setError(cause instanceof Error ? cause.message : "The local action failed."); }
    finally { if (mounted.current) setBusy(false); }
  };

  return <WorkspaceModal title="Workspace activity" onClose={onClose} wide>
    <div className="space-y-5">
      <div className="flex items-start justify-between gap-3">
        <p className="text-sm text-muted">Tasks for this conversation and media saved on this computer.</p>
        <button type="button" aria-label="Refresh activity" onClick={() => void refresh()} className="rounded-md border border-line-soft p-2 text-muted"><RefreshCw size={16} /></button>
      </div>
      {error && <p role="alert" className="rounded-lg border border-danger/30 bg-danger/5 p-3 text-sm text-danger-ink">{error}</p>}
      {!loaded && <p className="flex items-center gap-2 text-sm text-muted"><Loader2 size={16} className="animate-spin" /> Loading activity…</p>}
      <section className="space-y-3">
        <h3 className="text-sm font-semibold">Conversation tasks</h3>
        {loaded && !runs.length && <p className="text-sm text-muted">Tasks will appear here when you ask Workspace to do something.</p>}
        {runs.map((run) => <article key={run.id} className="space-y-2 rounded-lg border border-line-soft bg-soft/40 p-4">
          <div className="flex justify-between gap-3"><p className="whitespace-pre-wrap text-sm font-medium">{run.query}</p><span className="shrink-0 text-xs text-muted">{run.status.replaceAll("_", " ")}</span></div>
          {run.status === "queued" && run.message && <p className="text-sm text-muted">{run.message}</p>}
          {run.result?.answer && <p className="whitespace-pre-wrap text-sm leading-6">{run.result.answer}</p>}
          {Array.isArray(run.result?.sources) && run.result.sources.length > 0 && <p className="text-xs text-muted">Sources: {run.result.sources.map((source: { source: string }) => source.source).join(", ")}</p>}
          {run.error && <p role="alert" className="text-sm text-danger-ink">{run.error}</p>}
          <div className="flex flex-wrap items-center gap-4">
            {run.status === "awaiting_input" && run.question && <button type="button" disabled={busy} onClick={() => onQuestion(run)}
              className="inline-flex items-center gap-1.5 rounded-md border border-brand/25 bg-brand/10 px-3 py-2 text-xs font-medium text-brand-ink"><MessageCircleQuestion size={14} /> Answer question</button>}
            {["queued", "running", "awaiting_input"].includes(run.status) && <button type="button" disabled={busy} className="text-xs text-danger-ink" onClick={() => void act(async () => { await api.cancelLocalRun(run.id); onCancelled(run.id); })}>Cancel task</button>}
          </div>
          <details className="text-xs text-muted"><summary className="cursor-pointer">Tool activity · {run.model_calls} model calls · {run.tool_calls} tools</summary>
            <ul className="mt-2 space-y-1">{run.trace.map((item, i) => <li key={i}>{item.tool}{item.action ? ` → ${item.action}` : ""} · {item.status}{item.model ? ` · ${item.model}` : ""}</li>)}</ul>
          </details>
        </article>)}
      </section>
      <section className="space-y-3 border-t border-line-soft pt-5">
        <h3 className="text-sm font-semibold">Generated media</h3>
        {loaded && !media.length && <p className="text-sm text-muted">Completed outputs can be downloaded or deleted here.</p>}
        <div className="grid gap-4 sm:grid-cols-2">{media.map((job) => <div key={job.id}>
          <LocalMediaCard initialJob={job} watch={false} />
          {!["queued", "running"].includes(job.status) && !job.worker_active && <button type="button" disabled={busy} className="mt-2 text-xs text-danger-ink" onClick={() => void act(() => api.deleteLocalMedia(job.id))}>Delete output</button>}
        </div>)}</div>
      </section>
    </div>
  </WorkspaceModal>;
}
