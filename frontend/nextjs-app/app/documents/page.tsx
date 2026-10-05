"use client";

import { useEffect, useRef, useState } from "react";
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
  const [previewLoading, setPreviewLoading] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const activeSourceRef = useRef<string | null>(null);
  const previewRequestRef = useRef(0);
  const loadRequestRef = useRef(0);

  const load = async () => {
    const requestId = ++loadRequestRef.current;
    setLoading(true);
    setMessage("");
    try {
      const docs = await api.ragDocuments();
      if (requestId !== loadRequestRef.current) return;
      setDocuments(docs);
      if (activeSourceRef.current && !docs.some((doc: DocumentItem) => doc.source === activeSourceRef.current)) {
        previewRequestRef.current += 1;
        activeSourceRef.current = null;
        setActive(null);
        setPreview(null);
        setPreviewLoading(false);
      }
    } catch (error) {
      if (requestId === loadRequestRef.current) setMessage(error instanceof Error ? error.message : "Failed to load documents");
    } finally {
      if (requestId === loadRequestRef.current) setLoading(false);
    }
  };

  const openPreview = async (doc: DocumentItem) => {
    const requestId = ++previewRequestRef.current;
    activeSourceRef.current = doc.source;
    setActive(doc);
    setPreview(null);
    setPreviewLoading(true);
    setMessage("");
    try {
      const result = await api.ragDocumentPreview(doc.source);
      if (requestId === previewRequestRef.current) setPreview(result);
    } catch (error) {
      if (requestId === previewRequestRef.current) setMessage(error instanceof Error ? error.message : "Failed to load preview");
    } finally {
      if (requestId === previewRequestRef.current) setPreviewLoading(false);
    }
  };

  const deleteDoc = async (doc: DocumentItem) => {
    if (!window.confirm(`Delete ${doc.title} from the knowledge base?`)) return;
    setDeleting(true);
    setMessage("");
    try {
      const res = await api.ragDeleteDocument(doc.source);
      if (activeSourceRef.current === doc.source) {
        previewRequestRef.current += 1;
        activeSourceRef.current = null;
        setActive(null);
        setPreview(null);
        setPreviewLoading(false);
      }
      await load();
      setMessage(`Deleted ${res.deleted_chunks ?? 0} chunks from ${doc.title}`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Delete failed");
    } finally {
      setDeleting(false);
    }
  };

  useEffect(() => {
    void load();
    return () => {
      loadRequestRef.current += 1;
      previewRequestRef.current += 1;
    };
  }, []);

  return (
    <div className="mx-auto max-w-6xl px-4 py-6 sm:px-6 lg:py-8">
      <div className="mb-6 flex items-center justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold text-ink">Document Library</h1>
          <p className="text-sm text-muted">Manage the files and notes available to 2nd Brain.</p>
        </div>
        <button
          onClick={load}
          className="flex items-center gap-2 rounded-md border border-line px-3 py-2 text-sm text-ink-subtle transition hover:border-line-strong hover:text-ink"
        >
          <RefreshCw size={15} className={loading ? "animate-spin" : ""} />
          Refresh
        </button>
      </div>

      {message && (
        <div className="mb-4 rounded-md border border-line bg-panel px-3 py-2 text-sm text-ink-subtle">
          {message}
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-[420px_1fr]">
        <section className="min-h-[24rem] rounded-md border border-line-soft bg-panel/60">
          <div className="border-b border-line-soft px-4 py-3 text-sm font-semibold text-ink">
            Documents
          </div>
          {loading ? (
            <div className="grid h-64 place-items-center text-muted">
              <Loader2 className="animate-spin" size={22} />
            </div>
          ) : documents.length === 0 ? (
            <div className="px-4 py-12 text-center text-sm text-muted">
              No documents indexed yet.
            </div>
          ) : (
            <div className="divide-y divide-line-soft">
              {documents.map((doc) => (
                <button
                  key={doc.source}
                  onClick={() => openPreview(doc)}
                  className={`flex w-full items-start gap-3 px-4 py-3 text-left transition hover:bg-soft/70 ${
                    active?.source === doc.source ? "bg-soft/80" : ""
                  }`}
                >
                  <FileText size={18} className="mt-1 shrink-0 text-analytic" />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-medium text-ink">{doc.title}</span>
                    <span className="mt-1 block text-xs text-muted">{doc.chunks} chunks / {doc.type}</span>
                  </span>
                </button>
              ))}
            </div>
          )}
        </section>

        <section className="min-h-[24rem] rounded-md border border-line-soft bg-panel/60">
          <div className="flex items-center justify-between border-b border-line-soft px-4 py-3">
            <div>
              <h2 className="text-sm font-semibold text-ink">{active?.title || "Preview"}</h2>
              {active && <p className="text-xs text-muted">{active.chunks} chunks indexed</p>}
            </div>
            {active && (
              <button
                onClick={() => deleteDoc(active)}
                disabled={deleting}
                className="grid h-9 w-9 place-items-center rounded-md border border-line text-muted transition hover:border-danger hover:text-danger-ink"
                aria-label="Delete document"
                title="Delete document"
              >
                <Trash2 size={15} />
              </button>
            )}
          </div>
          <div className="p-4">
            {!active ? (
              <div className="rounded-md border border-dashed border-line px-4 py-16 text-center text-sm text-muted">
                Select a document to preview extracted text.
              </div>
            ) : previewLoading ? (
              <div className="grid h-48 place-items-center text-muted">
                <Loader2 className="animate-spin" size={22} />
              </div>
            ) : (
              <pre className="max-h-[34rem] overflow-auto whitespace-pre-wrap rounded-md bg-canvas p-4 text-sm leading-6 text-ink-subtle">
                {preview?.text || "No preview text available."}
              </pre>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}
