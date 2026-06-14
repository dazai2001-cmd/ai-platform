"use client";

import { useEffect, useState } from "react";
import { Activity, RefreshCw } from "lucide-react";
import { api } from "@/lib/api";

export default function AnalyticsPage() {
  const [summary, setSummary] = useState<any>(null);
  const [recent, setRecent] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  const refresh = async () => {
    setLoading(true);
    try {
      const [s, r] = await Promise.all([api.analyticsSummary(72), api.analyticsRecent(30)]);
      setSummary(s);
      setRecent(Array.isArray(r) ? r : []);
    } catch {
      setSummary(null);
      setRecent([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refresh();
  }, []);

  const cards = [
    ["Queries", summary?.total_queries ?? 0],
    ["Success", summary?.success_rate ? `${(summary.success_rate * 100).toFixed(0)}%` : "-"],
    ["Average", summary?.avg_latency_ms ? `${summary.avg_latency_ms}ms` : "-"],
    ["P95", summary?.p95_latency_ms ? `${summary.p95_latency_ms}ms` : "-"],
  ];

  return (
    <div className="mx-auto max-w-6xl px-4 py-6 sm:px-6 lg:py-8">
      <div className="mb-6 flex items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <div className="grid h-10 w-10 place-items-center rounded-md bg-cyan-400/10 text-cyan-300">
            <Activity size={20} />
          </div>
          <div>
            <h1 className="text-xl font-semibold text-white">Analytics</h1>
            <p className="text-sm text-slate-500">Recent agent usage and latency.</p>
          </div>
        </div>
        <button
          onClick={refresh}
          className="flex items-center gap-2 rounded-md border border-slate-700 px-3 py-2 text-sm text-slate-300 transition hover:border-slate-500 hover:text-white"
        >
          <RefreshCw size={15} className={loading ? "animate-spin" : ""} />
          Refresh
        </button>
      </div>

      <div className="mb-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {cards.map(([label, value]) => (
          <div key={label as string} className="rounded-md border border-slate-800 bg-slate-900/70 p-4">
            <p className="text-xs text-slate-500">{label}</p>
            <p className="mt-1 text-2xl font-semibold text-white">{value}</p>
          </div>
        ))}
      </div>

      <section className="overflow-hidden rounded-md border border-slate-800 bg-slate-900/70">
        <div className="border-b border-slate-800 px-4 py-3 text-sm font-semibold text-slate-200">Recent Queries</div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[720px] text-left text-sm">
            <thead className="bg-slate-950/70 text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th className="px-4 py-3">Agent</th>
                <th className="px-4 py-3">Model</th>
                <th className="px-4 py-3">Query</th>
                <th className="px-4 py-3">Latency</th>
                <th className="px-4 py-3">Status</th>
              </tr>
            </thead>
            <tbody>
              {recent.length === 0 ? (
                <tr>
                  <td className="px-4 py-8 text-center text-slate-500" colSpan={5}>No recent queries.</td>
                </tr>
              ) : (
                recent.map((event, index) => (
                  <tr key={`${event.timestamp}-${index}`} className="border-t border-slate-800">
                    <td className="px-4 py-3 text-slate-300">{event.agent}</td>
                    <td className="px-4 py-3 text-slate-400">{event.model}</td>
                    <td className="max-w-md truncate px-4 py-3 text-slate-300">{event.query}</td>
                    <td className="px-4 py-3 text-slate-400">{Math.round(event.latency_ms || 0)}ms</td>
                    <td className="px-4 py-3">
                      <span className={`rounded-md px-2 py-1 text-xs ${event.success === false ? "bg-rose-400/10 text-rose-200" : "bg-emerald-400/10 text-emerald-200"}`}>
                        {event.success === false ? "Failed" : "Ok"}
                      </span>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
