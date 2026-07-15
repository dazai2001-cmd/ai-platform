"use client";

import { useEffect, useRef, useState } from "react";
import { CheckCircle, RefreshCw, XCircle } from "lucide-react";
import { api } from "@/lib/api";

export default function SettingsPage() {
  const [health, setHealth] = useState<any>(null);
  const [stats, setStats] = useState<any>(null);
  const [modelSettings, setModelSettings] = useState<any>(null);
  const [saving, setSaving] = useState(false);
  const [saveStatus, setSaveStatus] = useState("");
  const [loading, setLoading] = useState(true);
  const saveRequestRef = useRef(0);

  const refresh = async () => {
    setLoading(true);
    try {
      const [h, s, m] = await Promise.all([api.health(), api.analyticsSummary(168), api.modelSettings()]);
      setHealth(h);
      setStats(s);
      setModelSettings(m);
    } catch {
      setHealth(null);
      setStats(null);
      setModelSettings(null);
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

  const taskModels = modelSettings?.task_models || health?.task_models || {};
  const availableModels = modelSettings?.available_models || health?.models || [];
  const runtime = health?.runtime || (health?.cloud_models ? "cloud" : "local");
  const isCloud = runtime === "cloud";
  const providerStatus = health?.provider_status || {};

  const persistTaskModels = async (nextTaskModels: Record<string, string>) => {
    const requestId = saveRequestRef.current + 1;
    saveRequestRef.current = requestId;
    setSaving(true);
    setSaveStatus("Saving...");
    try {
      const saved = await api.updateModelSettings(nextTaskModels);
      if (saveRequestRef.current === requestId) {
        setModelSettings(saved);
        setHealth(await api.health());
        setSaveStatus("Saved");
      }
    } catch {
      if (saveRequestRef.current === requestId) {
        setSaveStatus("Could not save");
      }
    } finally {
      if (saveRequestRef.current === requestId) {
        setSaving(false);
      }
    }
  };

  const setTaskModel = (task: string, model: string) => {
    const nextTaskModels = { ...taskModels, [task]: model };
    setModelSettings((current: any) => ({
      ...(current || {}),
      task_models: nextTaskModels,
      available_models: availableModels,
    }));
    persistTaskModels(nextTaskModels);
  };

  const saveModels = async () => {
    await persistTaskModels(taskModels);
  };

  const resetModels = async () => {
    setSaving(true);
    setSaveStatus("Resetting...");
    try {
      setModelSettings(await api.resetModelSettings());
      setHealth(await api.health());
      setSaveStatus("Defaults restored");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="mx-auto max-w-5xl px-4 py-6 sm:px-6 lg:py-8">
      <div className="mb-6 flex items-center justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold text-ink">Status</h1>
          <p className="text-sm text-muted">Runtime health and routing configuration.</p>
        </div>
        <button
          onClick={refresh}
          className="flex items-center gap-2 rounded-md border border-line px-3 py-2 text-sm text-ink-subtle transition hover:border-line-strong hover:text-ink"
        >
          <RefreshCw size={15} className={loading ? "animate-spin" : ""} />
          Refresh
        </button>
      </div>

      <div className="grid gap-4 lg:grid-cols-[1fr_1fr]">
        <section className="rounded-md border border-line-soft bg-panel/70 p-5">
          <h2 className="mb-4 text-sm font-semibold text-ink">{isCloud ? "Cloud Models" : "Ollama"}</h2>
          <div className="mb-4 flex items-center gap-2">
            {health?.status === "ok" ? (
              <CheckCircle size={17} className="text-success-ink" />
            ) : (
              <XCircle size={17} className="text-danger-ink" />
            )}
            <span className="text-sm text-ink-subtle">
              {health?.status === "ok" ? (isCloud ? "Cloud runtime ready" : "Connected") : "Not reachable"}
            </span>
          </div>
          <p className="mb-3 text-xs uppercase tracking-wide text-muted-soft">Runtime: {runtime}</p>
          <div className="flex flex-wrap gap-2">
            {health?.models?.length ? (
              health.models.map((m: string) => (
                <span key={m} className="rounded-md bg-soft px-2 py-1 text-xs text-ink-subtle">{m}</span>
              ))
            ) : (
              <span className="text-sm text-muted">No model list available.</span>
            )}
          </div>
          {isCloud && (
            <div className="mt-4 space-y-2 border-t border-line-soft pt-4">
              {Object.entries(providerStatus).map(([provider, status]: [string, any]) => (
                <div key={provider} className="flex items-center justify-between gap-3 text-xs">
                  <span className="capitalize text-muted">{provider}</span>
                  <span className={status?.api_key && status?.models ? "text-success-ink" : "text-warning-ink"}>
                    {status?.api_key ? `${status?.models || 0} models configured` : "API key missing"}
                  </span>
                </div>
              ))}
            </div>
          )}
        </section>

        <section className="rounded-md border border-line-soft bg-panel/70 p-5">
          <h2 className="mb-4 text-sm font-semibold text-ink">Analytics Snapshot</h2>
          <div className="grid grid-cols-2 gap-3">
            {cards.map(([label, value]) => (
              <div key={label as string} className="rounded-md bg-canvas/80 p-3">
                <p className="text-xs text-muted">{label}</p>
                <p className="mt-1 text-lg font-semibold text-ink">{value}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="rounded-md border border-line-soft bg-panel/70 p-5 lg:col-span-2">
          <div className="mb-4 flex items-center justify-between gap-4">
            <div>
              <h2 className="text-sm font-semibold text-ink">Agent Models</h2>
              <p className="mt-1 text-sm text-muted">Choose the default model each agent uses. Changes save automatically.</p>
              {saveStatus && <p className="mt-1 text-xs text-muted">{saveStatus}</p>}
            </div>
            <div className="flex gap-2">
              <button
                onClick={resetModels}
                disabled={saving}
                className="rounded-md border border-line px-3 py-2 text-sm text-ink-subtle transition hover:border-line-strong hover:text-ink disabled:opacity-50"
              >
                Reset
              </button>
              <button
                onClick={saveModels}
                disabled={saving}
                className="flex items-center gap-2 rounded-md bg-brand px-3 py-2 text-sm font-medium text-white transition hover:bg-brand-hover disabled:opacity-50"
              >
                <RefreshCw size={15} className={saving ? "animate-spin" : ""} />
                Save
              </button>
            </div>
          </div>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {Object.entries(taskModels).map(([task, model]) => (
              <label key={task} className="rounded-md bg-canvas/80 p-3">
                <span className="mb-2 block text-xs uppercase tracking-wide text-muted-soft">{task}</span>
                <select
                  value={model as string}
                  onChange={(e) => setTaskModel(task, e.target.value)}
                  className="w-full rounded-md border border-line bg-panel px-3 py-2 text-sm text-ink outline-none focus:border-analytic"
                >
                  {[model as string, ...availableModels.filter((m: string) => m !== model)].map((option: string) => (
                    <option key={option} value={option}>{option}</option>
                  ))}
                </select>
              </label>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}
