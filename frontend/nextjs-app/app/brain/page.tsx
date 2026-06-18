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
    setUploading(true);
    setUploadMsg("");
    setUploadError(false);
    try {
      const res = await api.ragUploadUrl(urlInput.trim());
      setUploadMsg(res.error || `URL ingested - ${res.chunks ?? 0} chunks`);
      setUploadError(Boolean(res.error));
      if (!res.error) setUrlInput("");
    } catch (error) {
      setUploadMsg(error instanceof Error ? error.message : "URL ingest failed");
      setUploadError(true);
    } finally {
      setUploading(false);
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
    <div className="flex h-[calc(100vh-88px)] min-h-[620px] flex-col lg:h-screen lg:min-h-0 lg:flex-row">
      <section className="border-b border-slate-800/70 bg-slate-950/58 p-4 backdrop-blur-xl lg:w-80 lg:shrink-0 lg:overflow-y-auto lg:border-b-0 lg:border-r lg:p-5">
        <div className="mb-5 flex items-center gap-3">
          <div className="grid h-9 w-9 place-items-center rounded-md border border-cyan-300/20 bg-cyan-300/10 text-cyan-200">
            <Brain size={18} />
          </div>
          <div>
            <h1 className="text-sm font-semibold text-white">Knowledge Base</h1>
            <p className="text-xs text-slate-500">PDFs, URLs, and notes</p>
          </div>
        </div>

        <div className="grid gap-4 sm:grid-cols-3 lg:grid-cols-1">
          <div className="app-panel rounded-md p-3">
            <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">PDF</div>
            <input ref={fileRef} type="file" accept=".pdf" className="hidden" onChange={handlePdfUpload} />
            <button
              onClick={() => fileRef.current?.click()}
              disabled={uploading}
              className="flex w-full items-center justify-center gap-2 rounded-md bg-slate-800 px-3 py-2 text-sm text-slate-200 transition duration-150 hover:-translate-y-0.5 hover:bg-slate-700 active:translate-y-0 disabled:opacity-50"
            >
              <Upload size={15} />
              Upload PDF
            </button>
          </div>

          <div className="app-panel rounded-md p-3">
            <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">URL</div>
            <input
              className="app-input mb-2 w-full rounded-md px-3 py-2 text-sm placeholder:text-slate-600"
              placeholder="https://..."
              value={urlInput}
              onChange={(e) => setUrlInput(e.target.value)}
            />
            <button
              onClick={handleUrlIngest}
              disabled={uploading || !urlInput.trim()}
              className="flex w-full items-center justify-center gap-2 rounded-md bg-slate-800 px-3 py-2 text-sm text-slate-200 transition duration-150 hover:-translate-y-0.5 hover:bg-slate-700 active:translate-y-0 disabled:opacity-50"
            >
              <LinkIcon size={15} />
              Ingest URL
            </button>
          </div>

          <div className="app-panel rounded-md p-3">
            <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Note</div>
            <textarea
              className="app-input mb-2 h-24 w-full resize-none rounded-md px-3 py-2 text-sm placeholder:text-slate-600"
              placeholder="Paste notes or ideas..."
              value={textInput}
              onChange={(e) => setTextInput(e.target.value)}
            />
            <button
              onClick={handleTextIngest}
              disabled={uploading || !textInput.trim()}
              className="flex w-full items-center justify-center gap-2 rounded-md bg-slate-800 px-3 py-2 text-sm text-slate-200 transition duration-150 hover:-translate-y-0.5 hover:bg-slate-700 active:translate-y-0 disabled:opacity-50"
            >
              <FileText size={15} />
              Save Note
            </button>
          </div>
        </div>

        {uploadMsg && (
          <div className={`mt-4 rounded-md border px-3 py-2 text-xs ${
            uploadError
              ? "border-red-400/30 bg-red-400/10 text-red-200"
              : "border-emerald-400/20 bg-emerald-400/10 text-emerald-200"
          }`}>
            {uploadMsg}
            {uploading && (
              <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-slate-800">
                <div
                  className="h-full rounded-full bg-cyan-300 transition-all"
                  style={{ width: `${Math.max(uploadProgress, 8)}%` }}
                />
              </div>
            )}
          </div>
        )}
      </section>

      <section className="flex min-h-0 flex-1 flex-col">
        <header className="border-b border-slate-800/70 bg-slate-950/30 px-5 py-4 backdrop-blur">
          <h2 className="text-lg font-semibold text-white">2nd Brain</h2>
          <p className="text-sm text-slate-500">Ask questions across your documents, notes, and URLs.</p>
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
