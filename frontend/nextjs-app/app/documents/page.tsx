"use client";

import { useEffect, useState } from "react";
import { FileText, Loader2, RefreshCw, Trash2 } from "lucide-react";
import { api } from "@/lib/api";

type DocumentItem = {
  source: string;
  title: string;
  chunks: number;
  type: string;
  preview: string;
};

export default function DocumentsPage() {
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [active, setActive] = useState<DocumentItem | null>(null);
  const [preview, setPreview] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState("");

  const load = async () => {
    setLoading(true);
    setMessage("");
    try {
      const docs = await api.ragDocuments();
      setDocuments(docs);
      if (active && !docs.some((doc: DocumentItem) => doc.source === active.source)) {
        setActive(null);
        setPreview(null);
      }
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Failed to load documents");
    } finally {
      setLoading(false);
    }
  };

  const openPreview = async (doc: DocumentItem) => {
    setActive(doc);
    setPreview(null);
    setMessage("");
    try {
      setPreview(await api.ragDocumentPreview(doc.source));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Failed to load preview");
    }
  };

  const deleteDoc = async (doc: DocumentItem) => {
    if (!window.confirm(`Delete ${doc.title} from the knowledge base?`)) return;
    setMessage("");
    try {
      const res = await api.ragDeleteDocument(doc.source);
      setMessage(`Deleted ${res.deleted_chunks ?? 0} chunks from ${doc.title}`);
      setActive(null);
      setPreview(null);
      await load();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Delete failed");
    }
  };

  useEffect(() => {
    load();
  }, []);

  return (
    <div className="mx-auto max-w-6xl px-4 py-6 sm:px-6 lg:py-8">
      <div className="mb-6 flex items-center justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold text-white">Document Library</h1>
          <p className="text-sm text-slate-500">Manage the files and notes available to 2nd Brain.</p>
        </div>
        <button
          onClick={load}
          className="flex items-center gap-2 rounded-md border border-slate-700 px-3 py-2 text-sm text-slate-300 transition hover:border-slate-500 hover:text-white"
        >
          <RefreshCw size={15} className={loading ? "animate-spin" : ""} />
          Refresh
        </button>
      </div>

      {message && (
        <div className="mb-4 rounded-md border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-300">
          {message}
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-[420px_1fr]">
        <section className="min-h-[24rem] rounded-md border border-slate-800 bg-slate-900/60">
          <div className="border-b border-slate-800 px-4 py-3 text-sm font-semibold text-slate-200">
            Documents
          </div>
          {loading ? (
            <div className="grid h-64 place-items-center text-slate-500">
              <Loader2 className="animate-spin" size={22} />
            </div>
          ) : documents.length === 0 ? (
            <div className="px-4 py-12 text-center text-sm text-slate-500">
              No documents indexed yet.
            </div>
          ) : (
            <div className="divide-y divide-slate-800">
              {documents.map((doc) => (
                <button
                  key={doc.source}
                  onClick={() => openPreview(doc)}
                  className={`flex w-full items-start gap-3 px-4 py-3 text-left transition hover:bg-slate-800/70 ${
                    active?.source === doc.source ? "bg-slate-800/80" : ""
                  }`}
                >
                  <FileText size={18} className="mt-1 shrink-0 text-cyan-300" />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-medium text-slate-100">{doc.title}</span>
                    <span className="mt-1 block text-xs text-slate-500">{doc.chunks} chunks / {doc.type}</span>
                  </span>
                </button>
              ))}
            </div>
          )}
        </section>

        <section className="min-h-[24rem] rounded-md border border-slate-800 bg-slate-900/60">
          <div className="flex items-center justify-between border-b border-slate-800 px-4 py-3">
            <div>
              <h2 className="text-sm font-semibold text-slate-200">{active?.title || "Preview"}</h2>
              {active && <p className="text-xs text-slate-500">{active.chunks} chunks indexed</p>}
            </div>
            {active && (
              <button
                onClick={() => deleteDoc(active)}
                className="grid h-9 w-9 place-items-center rounded-md border border-slate-700 text-slate-400 transition hover:border-rose-400 hover:text-rose-200"
                aria-label="Delete document"
                title="Delete document"
              >
                <Trash2 size={15} />
              </button>
            )}
          </div>
          <div className="p-4">
            {!active ? (
              <div className="rounded-md border border-dashed border-slate-700 px-4 py-16 text-center text-sm text-slate-500">
                Select a document to preview extracted text.
              </div>
            ) : !preview ? (
              <div className="grid h-48 place-items-center text-slate-500">
                <Loader2 className="animate-spin" size={22} />
              </div>
            ) : (
              <pre className="max-h-[34rem] overflow-auto whitespace-pre-wrap rounded-md bg-slate-950 p-4 text-sm leading-6 text-slate-300">
                {preview.text || "No preview text available."}
              </pre>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}
