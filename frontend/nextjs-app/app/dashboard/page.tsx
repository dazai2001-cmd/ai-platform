"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Database, Loader2, RefreshCw, Trash2, Upload } from "lucide-react";
import ChatWindow from "@/components/chat/ChatWindow";
import ChartRenderer from "@/components/charts/ChartRenderer";
import { api } from "@/lib/api";

type DatasetSummary = {
  name: string;
  rows: number;
  columns: string[];
};

type Notice = {
  kind: "success" | "error";
  text: string;
};

type DeleteError = {
  dataset: DatasetSummary;
  text: string;
};

const DATASET_LOAD_RETRY_DELAY_MS = 500;

function wait(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function normalizeDatasetName(filename: string) {
  const stem = filename.replace(/\.[^.]+$/, "");
  let normalized = stem
    .trim()
    .replace(/[^A-Za-z0-9_]+/g, "_")
    .replace(/_+/g, "_")
    .replace(/^_+|_+$/g, "")
    .toLowerCase();
  if (!normalized) normalized = "dataset";
  if (/^\d/.test(normalized)) normalized = `dataset_${normalized}`;
  return normalized.slice(0, 64).replace(/_+$/g, "") || "dataset";
}

function nextAvailableDatasetName(base: string, datasets: DatasetSummary[]) {
  const names = new Set(datasets.map((dataset) => dataset.name));
  if (!names.has(base)) return base;

  for (let index = 2; index < 10_000; index += 1) {
    const suffix = `_${index}`;
    const prefix = base.slice(0, 64 - suffix.length).replace(/_+$/g, "") || "dataset";
    const candidate = `${prefix}${suffix}`;
    if (!names.has(candidate)) return candidate;
  }
  return `${base.slice(0, 55)}_${crypto.randomUUID().slice(0, 8)}`;
}

function formatCell(value: unknown, column: string) {
  if (value === null || value === undefined || value === "") return "-";
  if (typeof value !== "number" || !Number.isFinite(value)) return String(value);

  const isPercentage = /(?:percentage|percent|pct)/i.test(column);
  const absolute = Math.abs(value);
  if (absolute > 0 && absolute < 0.000001) {
    const scientific = value.toExponential(4);
    return isPercentage ? `${scientific}%` : scientific;
  }
  const formatted = new Intl.NumberFormat(undefined, {
    maximumFractionDigits: Number.isInteger(value)
      ? 0
      : isPercentage && absolute >= 0.01
        ? 2
        : 6,
  }).format(value);
  return isPercentage ? `${formatted}%` : formatted;
}

export default function DashboardPage() {
  const [sessionId, setSessionId] = useState(() => crypto.randomUUID());
  const [datasets, setDatasets] = useState<DatasetSummary[]>([]);
  const [activeDataset, setActiveDataset] = useState("");
  const [loadingDatasets, setLoadingDatasets] = useState(true);
  const [datasetLoadError, setDatasetLoadError] = useState("");
  const [deleteError, setDeleteError] = useState<DeleteError | null>(null);
  const [deletingDataset, setDeletingDataset] = useState("");
  const [uploading, setUploading] = useState(false);
  const [uploadNotice, setUploadNotice] = useState<Notice | null>(null);
  const [lastChart, setLastChart] = useState<any>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const activeDatasetRef = useRef("");
  const datasetsRef = useRef<DatasetSummary[]>([]);
  const datasetLoadRequestRef = useRef(0);
  const datasetButtonRefs = useRef<Record<string, HTMLButtonElement | null>>({});
  const datasetListHeadingRef = useRef<HTMLDivElement>(null);

  const selectDataset = useCallback((name: string, forceReset = false) => {
    const changed = activeDatasetRef.current !== name;
    if (!changed && !forceReset) return;
    activeDatasetRef.current = name;
    if (changed) setActiveDataset(name);
    setLastChart(null);
    setSessionId(crypto.randomUUID());
  }, []);

  const loadDatasets = useCallback(async () => {
    const requestId = ++datasetLoadRequestRef.current;
    setLoadingDatasets(true);
    setDatasetLoadError("");
    let lastError: unknown;

    for (let attempt = 0; attempt < 2; attempt += 1) {
      try {
        const loaded = await api.biDatasets() as DatasetSummary[];
        if (requestId !== datasetLoadRequestRef.current) return null;
        datasetsRef.current = loaded;
        setDatasets(loaded);
        if (!loaded.some((dataset) => dataset.name === activeDatasetRef.current)) {
          selectDataset(loaded[0]?.name || "");
        }
        setLoadingDatasets(false);
        return loaded;
      } catch (error) {
        if (requestId !== datasetLoadRequestRef.current) return null;
        lastError = error;
        if (attempt === 0) await wait(DATASET_LOAD_RETRY_DELAY_MS);
      }
    }

    if (requestId !== datasetLoadRequestRef.current) return null;
    setLoadingDatasets(false);
    setDatasetLoadError(lastError instanceof Error ? lastError.message : "Could not load datasets");
    return null;
  }, [selectDataset]);

  useEffect(() => {
    void loadDatasets();
    return () => {
      datasetLoadRequestRef.current += 1;
    };
  }, [loadDatasets]);

  const handleSend = async (message: string, signal?: AbortSignal) => {
    if (!activeDataset) {
      throw new Error("Select or upload a dataset before asking a question.");
    }
    const requestedDataset = activeDataset;
    const res = await api.biAsk(message, sessionId, requestedDataset, signal);
    if (res.chart && !signal?.aborted && activeDatasetRef.current === requestedDataset) {
      setLastChart(res.chart);
    }
    return res;
  };

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    const baseName = normalizeDatasetName(file.name);
    const name = nextAvailableDatasetName(baseName, datasetsRef.current);
    if (
      name !== baseName
      && !window.confirm(`A dataset named "${baseName}" already exists. Upload this file as "${name}" instead?`)
    ) {
      if (fileRef.current) fileRef.current.value = "";
      return;
    }

    setUploading(true);
    setUploadNotice(null);
    setDeleteError(null);
    try {
      const res = await api.biUpload(file, name);
      if (res.error) {
        setUploadNotice({ kind: "error", text: res.error });
      } else {
        setUploadNotice({ kind: "success", text: `${res.name} loaded - ${res.rows} rows` });
        const uploaded = {
          name: res.name,
          rows: res.rows,
          columns: Array.isArray(res.columns) ? res.columns : [],
        };
        const updated = [
          uploaded,
          ...datasetsRef.current.filter((dataset) => dataset.name !== uploaded.name),
        ];
        datasetsRef.current = updated;
        setDatasets(updated);
        selectDataset(res.name, true);
        await loadDatasets();
      }
    } catch (error) {
      setUploadNotice({
        kind: "error",
        text: error instanceof Error ? error.message : "Upload failed",
      });
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const handleDelete = async (dataset: DatasetSummary, skipConfirmation = false) => {
    if (!skipConfirmation && !window.confirm(`Delete "${dataset.name}"? This cannot be undone.`)) return;

    setDeletingDataset(dataset.name);
    setDeleteError(null);
    try {
      await api.biDeleteDataset(dataset.name);
      const remaining = datasetsRef.current.filter((item) => item.name !== dataset.name);
      datasetsRef.current = remaining;
      setDatasets(remaining);
      const deletedActiveDataset = activeDatasetRef.current === dataset.name;
      const nextDataset = deletedActiveDataset ? remaining[0]?.name || "" : activeDatasetRef.current;
      if (deletedActiveDataset) selectDataset(nextDataset);
      setUploadNotice({ kind: "success", text: `${dataset.name} deleted` });
      window.setTimeout(() => {
        if (nextDataset) datasetButtonRefs.current[nextDataset]?.focus();
        else datasetListHeadingRef.current?.focus();
      }, 0);
    } catch (error) {
      setDeleteError({
        dataset,
        text: error instanceof Error ? error.message : "Could not delete dataset",
      });
    } finally {
      setDeletingDataset("");
    }
  };

  const renderResult = (msg: any) => {
    const rows = msg.rows || [];
    const columns = rows.length ? Object.keys(rows[0]) : [];
    return (
      <>
        {msg.chart && <ChartRenderer chart={msg.chart} />}
        {(msg.sql || rows.length > 0) && (
          <div className="mt-4 overflow-hidden rounded-md border border-line">
            {msg.sql && (
              <details className="border-b border-line bg-canvas">
                <summary className="cursor-pointer px-3 py-2 text-xs font-medium text-muted hover:text-ink">
                  View generated SQL
                </summary>
                <pre className="overflow-x-auto border-t border-line-soft p-3 text-xs text-brand-ink">{msg.sql}</pre>
              </details>
            )}
            {rows.length > 0 && (
              <div className="overflow-x-auto">
                <table className="min-w-full text-left text-xs">
                  <caption className="sr-only">Query results</caption>
                  <thead className="bg-canvas text-muted">
                    <tr>{columns.map((column) => <th key={column} scope="col" className="px-3 py-2">{column}</th>)}</tr>
                  </thead>
                  <tbody>
                    {rows.slice(0, 20).map((row: any, index: number) => (
                      <tr key={index} className="border-t border-line-soft">
                        {columns.map((column) => (
                          <td key={column} className="px-3 py-2 text-ink-subtle">{formatCell(row[column], column)}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
                {rows.length > 20 && (
                  <p className="border-t border-line-soft px-3 py-2 text-xs text-muted">
                    Showing 20 of {rows.length} rows.
                  </p>
                )}
              </div>
            )}
          </div>
        )}
      </>
    );
  };

  const datasetList = (() => {
    if (loadingDatasets) {
      return (
        <div role="status" className="flex items-center justify-center gap-2 rounded-md border border-line-soft px-3 py-6 text-sm text-muted">
          <Loader2 size={15} className="animate-spin" />
          Loading datasets...
        </div>
      );
    }
    if (datasets.length === 0 && !datasetLoadError) {
      return (
        <div className="rounded-md border border-dashed border-line px-3 py-6 text-center text-sm text-muted">
          No datasets loaded
        </div>
      );
    }
    return datasets.map((dataset) => (
      <div
        key={dataset.name}
        className={`flex overflow-hidden rounded-md border transition ${
          activeDataset === dataset.name
            ? "border-analytic bg-analytic text-white"
            : "border-line-soft bg-panel/70 text-ink-subtle hover:border-line"
        }`}
      >
        <button
          ref={(element) => {
            datasetButtonRefs.current[dataset.name] = element;
          }}
          type="button"
          onClick={() => selectDataset(dataset.name)}
          disabled={Boolean(deletingDataset) || uploading}
          className="min-w-0 flex-1 px-3 py-2 text-left text-sm disabled:cursor-wait disabled:opacity-60"
          aria-pressed={activeDataset === dataset.name}
        >
          <div className="truncate font-medium">{dataset.name}</div>
          <div className="text-xs opacity-75">{dataset.rows} rows / {dataset.columns.length} columns</div>
        </button>
        <button
          type="button"
          onClick={() => void handleDelete(dataset)}
          disabled={Boolean(deletingDataset) || uploading}
          className="grid w-10 shrink-0 place-items-center border-l border-current/20 transition hover:bg-danger/20 disabled:opacity-50"
          aria-busy={deletingDataset === dataset.name}
          aria-label={deletingDataset === dataset.name ? `Deleting ${dataset.name}` : `Delete ${dataset.name}`}
        >
          {deletingDataset === dataset.name ? <Loader2 size={15} className="animate-spin" /> : <Trash2 size={15} />}
        </button>
      </div>
    ));
  })();

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
          type="button"
          onClick={() => fileRef.current?.click()}
          disabled={loadingDatasets || uploading || Boolean(deletingDataset)}
          aria-busy={uploading}
          className="flex w-full items-center justify-center gap-2 rounded-md bg-brand px-3 py-2 text-sm font-medium text-white transition hover:bg-brand-hover disabled:opacity-50"
        >
          {uploading ? <Loader2 size={15} className="animate-spin" /> : <Upload size={15} />}
          {uploading ? "Uploading..." : "Upload CSV / Excel"}
        </button>

        {uploadNotice && (
          <div
            role={uploadNotice.kind === "error" ? "alert" : "status"}
            className={`mt-4 rounded-md border px-3 py-2 text-xs ${
              uploadNotice.kind === "error"
                ? "border-danger/30 bg-danger/10 text-danger-ink"
                : "border-success/20 bg-success/10 text-success-ink"
            }`}
          >
            {uploadNotice.text}
          </div>
        )}

        {datasetLoadError && (
          <div role="alert" className="mt-4 rounded-md border border-danger/30 bg-danger/10 px-3 py-2 text-xs text-danger-ink">
            <p>{datasetLoadError}</p>
            <button
              type="button"
              onClick={() => void loadDatasets()}
              disabled={uploading || Boolean(deletingDataset)}
              className="mt-2 inline-flex items-center gap-1.5 font-medium underline underline-offset-2 disabled:opacity-50"
            >
              <RefreshCw size={12} />
              Retry
            </button>
          </div>
        )}

        {deleteError && (
          <div role="alert" className="mt-4 rounded-md border border-danger/30 bg-danger/10 px-3 py-2 text-xs text-danger-ink">
            <p>{deleteError.text}</p>
            <button
              type="button"
              onClick={() => void handleDelete(deleteError.dataset, true)}
              disabled={loadingDatasets || Boolean(deletingDataset) || uploading}
              className="mt-2 inline-flex items-center gap-1.5 font-medium underline underline-offset-2 disabled:opacity-50"
            >
              <RefreshCw size={12} />
              Retry deleting {deleteError.dataset.name}
            </button>
          </div>
        )}

        <div className="mt-5">
          <div
            ref={datasetListHeadingRef}
            tabIndex={-1}
            className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted"
          >
            Loaded
          </div>
          <div className="space-y-2">
            {datasetList}
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
            {activeDataset ? `Active dataset: ${activeDataset}` : "Select or upload a dataset to start."}
          </p>
        </header>
        <div className="min-h-0 flex-1">
          <ChatWindow
            key={`${activeDataset}:${sessionId}`}
            onSend={handleSend}
            resetKey={`${activeDataset}:${sessionId}`}
            disabled={!activeDataset}
            placeholder={activeDataset ? "Show revenue by month as a bar chart..." : "Select or upload a dataset to start..."}
            emptyTitle="Ask a question about your data"
            renderExtra={renderResult}
          />
        </div>
      </section>
    </div>
  );
}
