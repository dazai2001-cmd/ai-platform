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
          <h2 className="mb-4 text-sm font-semibold text-slate-200">Model Providers</h2>
          <div className="mb-4 flex items-center gap-2">
            {health?.ollama || health?.cloud_models ? (
              <CheckCircle size={17} className="text-emerald-300" />
            ) : (
              <XCircle size={17} className="text-rose-300" />
            )}
            <span className="text-sm text-slate-300">
              {health?.ollama ? "Ollama connected" : "Ollama not reachable"}
              {health?.cloud_models ? " + cloud models configured" : ""}
            </span>
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
          <div className="mb-4 flex items-center justify-between gap-4">
            <div>
              <h2 className="text-sm font-semibold text-slate-200">Agent Models</h2>
              <p className="mt-1 text-sm text-slate-500">Choose the default model each agent uses. Changes save automatically.</p>
              {saveStatus && <p className="mt-1 text-xs text-slate-500">{saveStatus}</p>}
            </div>
            <div className="flex gap-2">
              <button
                onClick={resetModels}
                disabled={saving}
                className="rounded-md border border-slate-700 px-3 py-2 text-sm text-slate-300 transition hover:border-slate-500 hover:text-white disabled:opacity-50"
              >
                Reset
              </button>
              <button
                onClick={saveModels}
                disabled={saving}
                className="flex items-center gap-2 rounded-md bg-cyan-400 px-3 py-2 text-sm font-medium text-slate-950 transition hover:bg-cyan-300 disabled:opacity-50"
              >
                <RefreshCw size={15} className={saving ? "animate-spin" : ""} />
                Save
              </button>
            </div>
          </div>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {Object.entries(taskModels).map(([task, model]) => (
              <label key={task} className="rounded-md bg-slate-950/80 p-3">
                <span className="mb-2 block text-xs uppercase tracking-wide text-slate-600">{task}</span>
                <select
                  value={model as string}
                  onChange={(e) => setTaskModel(task, e.target.value)}
                  className="w-full rounded-md border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-100 outline-none focus:border-cyan-400"
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
