"use client";

import { useRef, useState } from "react";
import { Brain, FileText, Link as LinkIcon, Upload } from "lucide-react";
import ChatWindow from "@/components/chat/ChatWindow";
import { api } from "@/lib/api";

export default function BrainPage() {
  const [sessionId] = useState(() => crypto.randomUUID());
  const [uploading, setUploading] = useState(false);
  const [uploadMsg, setUploadMsg] = useState("");
  const [uploadError, setUploadError] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [urlInput, setUrlInput] = useState("");
  const [textInput, setTextInput] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  const handleSend = async (message: string) => api.ragAsk(message, sessionId);
  const handleStream = async (message: string) => api.ragAskStream(message, sessionId);

  const handlePdfUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    setUploadMsg("");
    setUploadError(false);
    setUploadProgress(5);
    try {
      const started = await api.ragUploadPdfAsync(file);
      setUploadMsg(`Processing ${file.name}...`);
      setUploadProgress(20);

      let complete = false;
      for (let attempt = 0; attempt < 120; attempt += 1) {
        await new Promise((resolve) => setTimeout(resolve, 1000));
        const job = await api.job(started.job_id);
        setUploadProgress(job.progress ?? 20);
        setUploadMsg(job.message || `Processing ${file.name}...`);

        if (job.status === "succeeded") {
          setUploadMsg(`${job.result?.filename || file.name} uploaded - ${job.result?.chunks ?? 0} chunks`);
          setUploadProgress(100);
          complete = true;
          break;
        }
        if (job.status === "failed") {
          throw new Error(job.error || "Upload processing failed");
        }
      }
      if (!complete) throw new Error("Upload is still processing. Check the document library in a moment.");
    } catch (error) {
      setUploadMsg(error instanceof Error ? error.message : "Upload failed");
      setUploadError(true);
    } finally {
      setUploading(false);
      setTimeout(() => setUploadProgress(0), 1200);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const handleUrlIngest = async () => {
    if (!urlInput.trim()) return;
    const url = urlInput.trim();
    setUploading(true);
    setUploadMsg("");
    setUploadError(false);
    setUploadProgress(5);
    try {
      const started = await api.ragUploadUrlAsync(url);
      setUploadMsg("Fetching and indexing URL...");
      setUploadProgress(20);

      let complete = false;
      for (let attempt = 0; attempt < 180; attempt += 1) {
        await new Promise((resolve) => setTimeout(resolve, 1000));
        const job = await api.job(started.job_id);
        setUploadProgress(job.progress ?? 20);
        setUploadMsg(job.message || "Fetching and indexing URL...");

        if (job.status === "succeeded") {
          setUploadMsg(`URL ingested - ${job.result?.chunks ?? 0} chunks`);
          setUploadProgress(100);
          setUrlInput("");
          complete = true;
          break;
        }
        if (job.status === "failed") {
          throw new Error(job.error || "URL ingestion failed");
        }
      }
      if (!complete) throw new Error("URL ingestion is still processing. Check Documents in a moment.");
    } catch (error) {
      setUploadMsg(error instanceof Error ? error.message : "URL ingest failed");
      setUploadError(true);
    } finally {
      setUploading(false);
      setTimeout(() => setUploadProgress(0), 1200);
    }
  };

  const handleTextIngest = async () => {
    if (!textInput.trim()) return;
    setUploading(true);
    setUploadMsg("");
    setUploadError(false);
    try {
      const res = await api.ragUploadText(textInput.trim(), "note");
      setUploadMsg(res.error || `Note saved - ${res.chunks ?? 0} chunks`);
      setUploadError(Boolean(res.error));
      if (!res.error) setTextInput("");
    } catch (error) {
      setUploadMsg(error instanceof Error ? error.message : "Text ingest failed");
      setUploadError(true);
    } finally {
      setUploading(false);
    }
  };

  return (
    <div className="flex min-h-[620px] flex-col lg:h-dvh lg:min-h-0 lg:flex-row">
      <section className="border-b border-line-soft bg-panel p-4 lg:w-80 lg:shrink-0 lg:overflow-y-auto lg:border-b-0 lg:border-r lg:p-5">
        <div className="mb-5 flex items-center gap-3">
          <div className="grid h-9 w-9 place-items-center rounded-md border border-brand/20 bg-brand/10 text-brand-ink">
            <Brain size={18} />
          </div>
          <div>
            <h1 className="text-sm font-semibold text-ink">Knowledge Base</h1>
            <p className="text-xs text-muted">PDFs, URLs, and notes</p>
          </div>
        </div>

        <div className="grid gap-4 sm:grid-cols-3 lg:grid-cols-1">
          <div className="app-panel rounded-md p-3">
            <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">PDF</div>
            <input ref={fileRef} type="file" accept=".pdf" className="hidden" onChange={handlePdfUpload} />
            <button
              onClick={() => fileRef.current?.click()}
              disabled={uploading}
              className="flex w-full items-center justify-center gap-2 rounded-md bg-soft px-3 py-2 text-sm text-ink transition duration-150 hover:bg-line-soft disabled:opacity-50"
            >
              <Upload size={15} />
              Upload PDF
            </button>
          </div>

          <div className="app-panel rounded-md p-3">
            <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">URL</div>
            <input
              className="app-input mb-2 w-full rounded-md px-3 py-2 text-sm placeholder:text-muted-soft"
              placeholder="https://..."
              value={urlInput}
              onChange={(e) => setUrlInput(e.target.value)}
            />
            <button
              onClick={handleUrlIngest}
              disabled={uploading || !urlInput.trim()}
              className="flex w-full items-center justify-center gap-2 rounded-md bg-soft px-3 py-2 text-sm text-ink transition duration-150 hover:bg-line-soft disabled:opacity-50"
            >
              <LinkIcon size={15} />
              Ingest URL
            </button>
          </div>

          <div className="app-panel rounded-md p-3">
            <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">Note</div>
            <textarea
              className="app-input mb-2 h-24 w-full resize-none rounded-md px-3 py-2 text-sm placeholder:text-muted-soft"
              placeholder="Paste notes or ideas..."
              value={textInput}
              onChange={(e) => setTextInput(e.target.value)}
            />
            <button
              onClick={handleTextIngest}
              disabled={uploading || !textInput.trim()}
              className="flex w-full items-center justify-center gap-2 rounded-md bg-soft px-3 py-2 text-sm text-ink transition duration-150 hover:bg-line-soft disabled:opacity-50"
            >
              <FileText size={15} />
              Save Note
            </button>
          </div>
        </div>

        {uploadMsg && (
          <div className={`mt-4 rounded-md border px-3 py-2 text-xs ${
            uploadError
              ? "border-danger/30 bg-danger/10 text-danger-ink"
              : "border-success/20 bg-success/10 text-success-ink"
          }`}>
            {uploadMsg}
            {uploading && (
              <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-soft">
                <div
                  className="h-full rounded-full bg-brand transition-all"
                  style={{ width: `${Math.max(uploadProgress, 8)}%` }}
                />
              </div>
            )}
          </div>
        )}
      </section>

      <section className="flex min-h-0 flex-1 flex-col">
        <header className="border-b border-line-soft bg-panel px-5 py-4">
          <h2 className="text-lg font-semibold text-ink">2nd Brain</h2>
          <p className="text-sm text-muted">Ask questions across your documents, notes, and URLs.</p>
        </header>
        <div className="min-h-0 flex-1">
          <ChatWindow
            onSend={handleSend}
            onStream={handleStream}
            streamMeta={{ route: "rag" }}
            placeholder="What should I know about..."
            emptyTitle="Ask your knowledge base"
          />
        </div>
      </section>
    </div>
  );
}
