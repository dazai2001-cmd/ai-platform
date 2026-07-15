"use client";

import type { ChangeEvent, DragEvent } from "react";
import { useEffect, useRef, useState } from "react";
import { CheckCircle2, FileText, Loader2, RefreshCw, Upload, XCircle } from "lucide-react";
import { api, type CareerProfileImportResult } from "@/lib/api";

const MAX_CV_BYTES = 10 * 1024 * 1024;
const ACCEPTED_CV_TYPES = ".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document";

type ImportState =
  | { kind: "idle" }
  | { kind: "reading"; filename: string }
  | { kind: "success"; result: CareerProfileImportResult }
  | { kind: "error"; message: string; retryFile: File | null };

type CvProfileImportProps = {
  disabled?: boolean;
  hasProfile: boolean;
  beforeImport?: () => Promise<void>;
  onBusyChange?: (busy: boolean) => void;
  onImported: (profile: CareerProfileImportResult) => void;
};

export default function CvProfileImport({
  disabled = false,
  hasProfile,
  beforeImport,
  onBusyChange,
  onImported,
}: CvProfileImportProps) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const previousHasProfileRef = useRef(hasProfile);
  const [state, setState] = useState<ImportState>({ kind: "idle" });
  const [isDragging, setIsDragging] = useState(false);
  const isReading = state.kind === "reading";
  const controlsDisabled = disabled || isReading;

  useEffect(() => {
    if (previousHasProfileRef.current && !hasProfile) setState({ kind: "idle" });
    previousHasProfileRef.current = hasProfile;
  }, [hasProfile]);

  const importFile = async (file: File) => {
    const validationError = validateCvFile(file);
    if (validationError) {
      setState({ kind: "error", message: validationError, retryFile: null });
      return;
    }

    setState({ kind: "reading", filename: file.name });
    onBusyChange?.(true);
    try {
      await beforeImport?.();
      const result = await api.careerImportProfile(file);
      onImported(result);
      setState({ kind: "success", result });
    } catch (error) {
      setState({
        kind: "error",
        message: error instanceof Error ? error.message : "The CV could not be read. Try the file again.",
        retryFile: file,
      });
    } finally {
      onBusyChange?.(false);
    }
  };

  const handleFileChange = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (file) void importFile(file);
  };

  const handleDragOver = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    if (controlsDisabled) return;
    event.dataTransfer.dropEffect = "copy";
    setIsDragging(true);
  };

  const handleDragLeave = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setIsDragging(false);
  };

  const handleDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setIsDragging(false);
    if (controlsDisabled) return;

    if (event.dataTransfer.files.length !== 1) {
      setState({ kind: "error", message: "Choose one CV file at a time.", retryFile: null });
      return;
    }
    void importFile(event.dataTransfer.files[0]);
  };

  const retryImport = () => {
    if (state.kind === "error" && state.retryFile) void importFile(state.retryFile);
  };

  return (
    <section
      className="relative mb-3 overflow-hidden rounded-md border border-brand/20 bg-brand-soft/35 p-3 pl-4"
      aria-labelledby="cv-file-import-title"
      aria-busy={isReading}
    >
      <div aria-hidden="true" className="absolute inset-y-0 left-0 w-1 bg-brand" />

      <div className="mb-2 flex items-start justify-between gap-3">
        <div>
          <h2 id="cv-file-import-title" className="text-xs font-semibold uppercase tracking-wide text-brand-ink">
            Import CV file
          </h2>
          <p id="cv-file-import-description" className="mt-1 text-xs leading-5 text-ink-subtle">
            The app extracts editable text, then discards the uploaded file. The model only receives the text.
          </p>
        </div>
        <FileText aria-hidden="true" className="mt-0.5 shrink-0 text-brand" size={18} />
      </div>

      <div
        role="group"
        aria-label="CV file drop zone"
        className={`rounded-md border border-dashed px-3 py-3 transition ${
          isDragging
            ? "border-brand bg-brand/10"
            : "border-line bg-panel/75 hover:border-brand/60"
        }`}
        onDragEnter={handleDragOver}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
      >
        <input
          ref={fileInputRef}
          type="file"
          accept={ACCEPTED_CV_TYPES}
          className="sr-only"
          aria-label="Choose CV file"
          aria-describedby="cv-file-import-description cv-file-import-help"
          onChange={handleFileChange}
          disabled={controlsDisabled}
        />
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <div className="text-xs text-muted">
            <span className="font-medium text-ink-subtle">Drop a PDF or Word (.docx) file here</span>
            <span className="block text-muted-soft">Maximum 10 MB</span>
          </div>
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            disabled={controlsDisabled}
            aria-describedby="cv-file-import-description cv-file-import-help"
            className="inline-flex shrink-0 items-center justify-center gap-1.5 rounded-md bg-brand px-3 py-2 text-xs font-semibold text-white transition hover:bg-brand-hover disabled:cursor-not-allowed disabled:opacity-50"
          >
            {isReading ? <Loader2 aria-hidden="true" className="animate-spin" size={14} /> : <Upload aria-hidden="true" size={14} />}
            {isReading ? "Reading..." : hasProfile || state.kind === "success" ? "Replace file" : "Choose file"}
          </button>
        </div>
      </div>

      <p id="cv-file-import-help" className="mt-2 text-[11px] leading-4 text-muted-soft">
        Legacy Word .doc files are not supported. Save the file as .docx or export it as PDF first.
      </p>

      <div className="mt-2" aria-live="polite" aria-atomic="true">
        {state.kind === "reading" && (
          <div role="status" className="flex items-center gap-2 rounded-md border border-analytic/20 bg-analytic-soft px-2.5 py-2 text-xs text-analytic-hover">
            <Loader2 aria-hidden="true" className="animate-spin" size={14} />
            Reading {state.filename} and extracting editable text...
          </div>
        )}

        {state.kind === "success" && (
          <div role="status" className="flex items-start gap-2 rounded-md border border-success/20 bg-success-soft px-2.5 py-2 text-xs text-success-ink">
            <CheckCircle2 aria-hidden="true" className="mt-0.5 shrink-0" size={14} />
            <div className="min-w-0">
              <div className="truncate font-semibold">{state.result.filename}</div>
              <div className="mt-0.5 text-success-ink/80">{formatImportMetadata(state.result)}</div>
            </div>
          </div>
        )}

        {state.kind === "error" && (
          <div role="alert" className="flex items-start justify-between gap-3 rounded-md border border-danger/25 bg-danger-soft px-2.5 py-2 text-xs text-danger-ink">
            <div className="flex min-w-0 items-start gap-2">
              <XCircle aria-hidden="true" className="mt-0.5 shrink-0" size={14} />
              <span>{state.message}</span>
            </div>
            {state.retryFile && (
              <button
                type="button"
                onClick={retryImport}
                disabled={controlsDisabled}
                className="inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-1 font-semibold text-danger-ink transition hover:bg-danger/10 disabled:opacity-50"
              >
                <RefreshCw aria-hidden="true" size={12} />
                Retry
              </button>
            )}
          </div>
        )}
      </div>
    </section>
  );
}

function validateCvFile(file: File): string | null {
  const lowerName = file.name.toLowerCase();
  if (lowerName.endsWith(".doc")) {
    return "Legacy .doc files are not supported. Save the CV as .docx or export it as PDF, then try again.";
  }
  if (!lowerName.endsWith(".pdf") && !lowerName.endsWith(".docx")) {
    return "Choose a PDF or Word .docx file.";
  }
  if (file.size === 0) return "The selected file is empty.";
  if (file.size > MAX_CV_BYTES) return "The selected file is larger than the 10 MB limit.";
  return null;
}

function formatImportMetadata(result: CareerProfileImportResult): string {
  const details = [
    result.file_type.toUpperCase(),
    `${result.characters.toLocaleString()} characters`,
  ];
  if (typeof result.pages === "number") {
    details.push(`${result.pages} ${result.pages === 1 ? "page" : "pages"}`);
  }
  if (result.file_type === "pdf") {
    details.push(result.used_ocr ? "OCR used" : "OCR not needed");
  }
  return details.join(" / ");
}
