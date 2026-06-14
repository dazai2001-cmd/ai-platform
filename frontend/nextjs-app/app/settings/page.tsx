"use client";

import { useEffect, useState } from "react";
import { CheckCircle, RefreshCw, XCircle } from "lucide-react";
import { api } from "@/lib/api";

export default function SettingsPage() {
  const [health, setHealth] = useState<any>(null);
  const [stats, setStats] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  const refresh = async () => {
    setLoading(true);
    try {
      const [h, s] = await Promise.all([api.health(), api.analyticsSummary(168)]);
      setHealth(h);
      setStats(s);
    } catch {
      setHealth(null);
      setStats(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refresh();
  }, []);

  const cards = [
    ["Total Queries", stats?.total_queries ?? 0],
    ["Success Rate", stats?.success_rate ? `${(stats.success_rate * 100).toFixed(0)}%` : "-"],
    ["Avg Latency", stats?.avg_latency_ms ? `${stats.avg_latency_ms}ms` : "-"],
    ["P95 Latency", stats?.p95_latency_ms ? `${stats.p95_latency_ms}ms` : "-"],
  ];

  return (
    <div className="mx-auto max-w-5xl px-4 py-6 sm:px-6 lg:py-8">
      <div className="mb-6 flex items-center justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold text-white">Status</h1>
          <p className="text-sm text-slate-500">Runtime health and routing configuration.</p>
        </div>
        <button
          onClick={refresh}
          className="flex items-center gap-2 rounded-md border border-slate-700 px-3 py-2 text-sm text-slate-300 transition hover:border-slate-500 hover:text-white"
        >
          <RefreshCw size={15} className={loading ? "animate-spin" : ""} />
          Refresh
        </button>
      </div>

      <div className="grid gap-4 lg:grid-cols-[1fr_1fr]">
        <section className="rounded-md border border-slate-800 bg-slate-900/70 p-5">
          <h2 className="mb-4 text-sm font-semibold text-slate-200">Ollama</h2>
          <div className="mb-4 flex items-center gap-2">
            {health?.ollama ? (
              <CheckCircle size={17} className="text-emerald-300" />
            ) : (
              <XCircle size={17} className="text-rose-300" />
            )}
            <span className="text-sm text-slate-300">{health?.ollama ? "Connected" : "Not reachable"}</span>
          </div>
          <div className="flex flex-wrap gap-2">
            {health?.models?.length ? (
              health.models.map((m: string) => (
                <span key={m} className="rounded-md bg-slate-800 px-2 py-1 text-xs text-slate-300">{m}</span>
              ))
            ) : (
              <span className="text-sm text-slate-500">No model list available.</span>
            )}
          </div>
        </section>

        <section className="rounded-md border border-slate-800 bg-slate-900/70 p-5">
          <h2 className="mb-4 text-sm font-semibold text-slate-200">Analytics Snapshot</h2>
          <div className="grid grid-cols-2 gap-3">
            {cards.map(([label, value]) => (
              <div key={label as string} className="rounded-md bg-slate-950/80 p-3">
                <p className="text-xs text-slate-500">{label}</p>
                <p className="mt-1 text-lg font-semibold text-white">{value}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="rounded-md border border-slate-800 bg-slate-900/70 p-5 lg:col-span-2">
          <h2 className="mb-3 text-sm font-semibold text-slate-200">Model Routing</h2>
          <p className="max-w-3xl text-sm leading-6 text-slate-500">
            Response model selection is centralized in the query router. The router classifies each user query as RAG,
            BI, memory, or general, then attaches the model for that task before the request reaches an agent.
          </p>
          {health?.task_models && (
            <div className="mt-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
              {Object.entries(health.task_models).map(([task, model]) => (
                <div key={task} className="rounded-md bg-slate-950/80 p-3">
                  <p className="text-xs uppercase tracking-wide text-slate-600">{task}</p>
                  <p className="mt-1 truncate text-sm font-medium text-slate-200">{model as string}</p>
                </div>
              ))}
            </div>
          )}
          <p className="mt-3 text-xs text-slate-600">
            Configure task models with TASK_MODELS_JSON in the root environment file.
          </p>
        </section>
      </div>
    </div>
  );
}
