"use client";

import { useEffect, useRef, useState } from "react";
import { Database, Upload } from "lucide-react";
import ChatWindow from "@/components/chat/ChatWindow";
import ChartRenderer from "@/components/charts/ChartRenderer";
import { api } from "@/lib/api";

export default function DashboardPage() {
  const [sessionId] = useState(() => crypto.randomUUID());
  const [datasets, setDatasets] = useState<any[]>([]);
  const [activeDataset, setActiveDataset] = useState("");
  const [uploading, setUploading] = useState(false);
  const [uploadMsg, setUploadMsg] = useState("");
  const [lastChart, setLastChart] = useState<any>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    api.biDatasets().then(setDatasets).catch(() => {});
  }, []);

  const handleSend = async (message: string) => {
    const res = await api.biAsk(message, sessionId, activeDataset || undefined);
    if (res.chart) setLastChart(res.chart);
    return res;
  };

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    setUploadMsg("");
    try {
      const name = file.name.replace(/\.[^.]+$/, "").replace(/[^A-Za-z0-9_]/g, "_");
      const res = await api.biUpload(file, name);
      setUploadMsg(res.error || `${res.name} loaded - ${res.rows} rows`);
      if (!res.error) {
        const updated = await api.biDatasets();
        setDatasets(updated);
        setActiveDataset(res.name);
      }
    } catch (error) {
      setUploadMsg(error instanceof Error ? error.message : "Upload failed");
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const renderResult = (msg: any) => {
    if (msg.chart) return <ChartRenderer chart={msg.chart} />;
    if (!msg.rows?.length) return null;

    const columns = Object.keys(msg.rows[0]);
    return (
      <div className="mt-4 overflow-hidden rounded-md border border-line">
        {msg.sql && (
          <details className="border-b border-line bg-canvas">
            <summary className="cursor-pointer px-3 py-2 text-xs font-medium text-muted hover:text-ink">
              View generated SQL
            </summary>
            <pre className="overflow-x-auto border-t border-line-soft p-3 text-xs text-brand-ink">{msg.sql}</pre>
          </details>
        )}
        <div className="overflow-x-auto">
          <table className="min-w-full text-left text-xs">
            <thead className="bg-canvas text-muted">
              <tr>{columns.map((column) => <th key={column} className="px-3 py-2">{column}</th>)}</tr>
            </thead>
            <tbody>
              {msg.rows.slice(0, 20).map((row: any, index: number) => (
                <tr key={index} className="border-t border-line-soft">
                  {columns.map((column) => (
                    <td key={column} className="px-3 py-2 text-ink-subtle">{String(row[column] ?? "")}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    );
  };

  return (
    <div className="flex h-[calc(100dvh-176px)] min-h-[560px] flex-col lg:h-dvh lg:min-h-0 lg:flex-row">
      <section className="border-b border-line-soft bg-panel p-4 lg:w-80 lg:shrink-0 lg:overflow-y-auto lg:border-b-0 lg:border-r lg:p-5">
        <div className="mb-5 flex items-center gap-3">
          <div className="grid h-9 w-9 place-items-center rounded-md bg-analytic/10 text-analytic">
            <Database size={18} />
          </div>
          <div>
            <h1 className="text-sm font-semibold text-ink">Datasets</h1>
            <p className="text-xs text-muted">CSV and Excel analysis</p>
          </div>
        </div>

        <input ref={fileRef} type="file" accept=".csv,.xlsx,.xls" className="hidden" onChange={handleUpload} />
        <button
          onClick={() => fileRef.current?.click()}
          disabled={uploading}
          className="flex w-full items-center justify-center gap-2 rounded-md bg-brand px-3 py-2 text-sm font-medium text-white transition hover:bg-brand-hover disabled:opacity-50"
        >
          <Upload size={15} />
          Upload CSV / Excel
        </button>

        {uploadMsg && (
          <div className="mt-4 rounded-md border border-success/20 bg-success/10 px-3 py-2 text-xs text-success-ink">
            {uploadMsg}
          </div>
        )}

        <div className="mt-5">
          <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">Loaded</div>
          <div className="space-y-2">
            {datasets.length === 0 ? (
              <div className="rounded-md border border-dashed border-line px-3 py-6 text-center text-sm text-muted">
                No datasets loaded
              </div>
            ) : (
              datasets.map((d) => (
                <button
                  key={d.name}
                  onClick={() => setActiveDataset(d.name)}
                  className={`w-full rounded-md border px-3 py-2 text-left text-sm transition ${
                    activeDataset === d.name
                      ? "border-analytic bg-analytic text-white"
                      : "border-line-soft bg-panel/70 text-ink-subtle hover:border-line"
                  }`}
                >
                  <div className="truncate font-medium">{d.name}</div>
                  <div className="text-xs opacity-75">{d.rows} rows / {d.columns.length} columns</div>
                </button>
              ))
            )}
          </div>
        </div>

        {lastChart && (
          <div className="mt-5">
            <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">Last Chart</div>
            <ChartRenderer chart={lastChart} compact />
          </div>
        )}
      </section>

      <section className="flex min-h-0 flex-1 flex-col">
        <header className="border-b border-line-soft bg-panel px-5 py-4">
          <h2 className="text-lg font-semibold text-ink">BI Dashboard</h2>
          <p className="text-sm text-muted">
            {activeDataset ? `Active dataset: ${activeDataset}` : "Upload a dataset to start."}
          </p>
        </header>
        <div className="min-h-0 flex-1">
          <ChatWindow
            onSend={handleSend}
            placeholder="Show revenue by month as a bar chart..."
            emptyTitle="Ask a question about your data"
            renderExtra={renderResult}
          />
        </div>
      </section>
    </div>
  );
}
