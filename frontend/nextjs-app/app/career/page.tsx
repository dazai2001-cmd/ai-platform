"use client";

import type { ReactNode } from "react";
import { useEffect, useState } from "react";
import Link from "next/link";
import { BriefcaseBusiness, Download, FileText, Gauge, Loader2, Settings, Sparkles } from "lucide-react";
import { api } from "@/lib/api";

export default function CareerPage() {
  const [cvText, setCvText] = useState("");
  const [jobDescription, setJobDescription] = useState("");
  const [careerModel, setCareerModel] = useState("");
  const [loadingAction, setLoadingAction] = useState<"" | "analysis" | "pack">("");
  const [result, setResult] = useState<any>(null);
  const [error, setError] = useState("");
  const loading = Boolean(loadingAction);

  useEffect(() => {
    let cancelled = false;
    api.modelSettings()
      .then((settings) => {
        if (!cancelled) setCareerModel(settings.task_models?.career || "");
      })
      .catch(() => {
        if (!cancelled) setCareerModel("");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const scoreFit = async () => {
    setLoadingAction("analysis");
    setError("");
    try {
      const analysis = await api.careerAnalyze(cvText, jobDescription);
      setResult({ analysis, model: analysis.model });
      if (analysis.model) setCareerModel(analysis.model);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to score fit");
    } finally {
      setLoadingAction("");
    }
  };

  const generate = async () => {
    setLoadingAction("pack");
    setError("");
    setResult(null);
    try {
      const pack = await api.careerPack(cvText, jobDescription);
      setResult(pack);
      if (pack.model) setCareerModel(pack.model);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to generate application pack");
    } finally {
      setLoadingAction("");
    }
  };

  const downloadPack = () => {
    if (!result) return;
    const lines = [
      "# Career Agent Output",
      "",
      "## Fit Analysis",
      "",
      `Score: ${result.analysis?.fit_score ?? "?"}/100`,
      "",
      result.analysis?.summary || "",
      "",
      "## Tailored CV",
      "",
      result.tailored_cv?.headline || "",
      "",
      result.tailored_cv?.professional_summary || "",
      "",
      ...(result.tailored_cv?.tailored_bullets || []).map((item: string) => `- ${item}`),
      "",
      "## Cover Letter",
      "",
      result.cover_letter?.cover_letter || "",
    ];
    const blob = new Blob([lines.join("\n")], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "career-agent-output.md";
    link.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="min-h-screen text-slate-100">
      <section className="border-b border-slate-800/70 bg-slate-950/30 px-5 py-5 backdrop-blur">
        <div className="flex items-center gap-3">
          <div className="grid h-10 w-10 place-items-center rounded-md border border-cyan-300/20 bg-cyan-300/10 text-cyan-200">
            <BriefcaseBusiness size={20} />
          </div>
          <div>
            <h1 className="text-lg font-semibold text-white">Career Agent</h1>
            <p className="text-sm text-slate-500">Qwen via Ollama for CV matching and application drafts</p>
          </div>
        </div>
      </section>

      <main className="grid gap-0 lg:grid-cols-[420px_1fr]">
        <section className="border-b border-slate-800/70 bg-slate-950/42 p-5 backdrop-blur-xl lg:min-h-[calc(100vh-82px)] lg:border-b-0 lg:border-r">
          <div className="app-panel mb-4 rounded-md p-3">
            <div className="mb-1 flex items-center justify-between gap-3">
              <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">Agent Model</span>
              <Link
                href="/settings"
                className="inline-flex items-center gap-1.5 rounded px-1.5 py-1 text-xs text-slate-500 transition hover:bg-slate-800 hover:text-cyan-200"
              >
                <Settings size={13} />
                Settings
              </Link>
            </div>
            <div className="truncate text-sm font-medium text-slate-100">{careerModel || "Loading..."}</div>
          </div>

          <label className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
            <FileText size={14} /> CV / Profile
          </label>
          <textarea
            value={cvText}
            onChange={(e) => setCvText(e.target.value)}
            className="app-input h-56 w-full resize-none rounded-md px-3 py-2 text-sm"
          />

          <label className="mb-2 mt-4 block text-xs font-semibold uppercase tracking-wide text-slate-500">
            Job Description
          </label>
          <textarea
            value={jobDescription}
            onChange={(e) => setJobDescription(e.target.value)}
            className="app-input h-56 w-full resize-none rounded-md px-3 py-2 text-sm"
          />

          <div className="mt-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-1">
            <button
              onClick={scoreFit}
              disabled={loading || !cvText.trim() || !jobDescription.trim()}
              className="flex items-center justify-center gap-2 rounded-md border border-slate-700 bg-slate-900/82 px-3 py-2 text-sm font-medium text-slate-100 transition duration-150 hover:-translate-y-0.5 hover:border-cyan-400 hover:text-cyan-200 active:translate-y-0 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {loadingAction === "analysis" ? <Loader2 className="animate-spin" size={16} /> : <Gauge size={16} />}
              Score Fit
            </button>
            <button
              onClick={generate}
              disabled={loading || !cvText.trim() || !jobDescription.trim()}
              className="flex items-center justify-center gap-2 rounded-md bg-cyan-300 px-3 py-2 text-sm font-medium text-slate-950 shadow-lg shadow-cyan-950/20 transition duration-150 hover:-translate-y-0.5 hover:bg-cyan-200 active:translate-y-0 disabled:cursor-not-allowed disabled:opacity-50 disabled:shadow-none"
            >
              {loadingAction === "pack" ? <Loader2 className="animate-spin" size={16} /> : <Sparkles size={16} />}
              Generate Pack
            </button>
          </div>

          {error && <div className="mt-4 rounded-md border border-red-400/30 bg-red-400/10 p-3 text-sm text-red-200">{error}</div>}
        </section>

        <section className="p-5">
          {!result ? (
            <div className="app-panel soft-fade-in rounded-md border-dashed px-4 py-16 text-center text-sm text-slate-500">
              Paste a CV and job description to generate a fit score, tailored CV points, and a cover letter.
            </div>
          ) : (
            <div className="space-y-4">
              <div className="flex justify-end">
                <button
                  onClick={downloadPack}
                  className="flex items-center gap-2 rounded-md border border-slate-700 bg-slate-900/72 px-3 py-2 text-sm text-slate-300 transition duration-150 hover:-translate-y-0.5 hover:border-cyan-400 hover:text-cyan-200 active:translate-y-0"
                >
                  <Download size={15} />
                  Download Markdown
                </button>
              </div>
              <Panel title="Fit Analysis">
                <div className="mb-3 text-4xl font-semibold text-cyan-300">
                  {result.analysis?.fit_score ?? "?"}
                  <span className="text-base text-slate-500"> / 100</span>
                </div>
                <p className="text-sm text-slate-300">{result.analysis?.summary}</p>
                <List title="Matched" items={result.analysis?.matched_skills} />
                <List title="Weak Signals" items={result.analysis?.missing_or_weak_signals} />
              </Panel>

              {result.tailored_cv && (
                <Panel title="Tailored CV">
                  <p className="mb-3 text-sm font-medium text-white">{result.tailored_cv.headline}</p>
                  <p className="text-sm text-slate-300">{result.tailored_cv.professional_summary}</p>
                  <List title="Bullets" items={result.tailored_cv.tailored_bullets} />
                  <List title="Do Not Claim" items={result.tailored_cv.do_not_claim} />
                </Panel>
              )}

              {result.cover_letter && (
                <Panel title="Cover Letter">
                  <pre className="whitespace-pre-wrap text-sm leading-6 text-slate-300">{result.cover_letter.cover_letter}</pre>
                </Panel>
              )}
            </div>
          )}
        </section>
      </main>
    </div>
  );
}

function Panel({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="app-panel soft-fade-in rounded-md p-4">
      <h2 className="mb-3 text-sm font-semibold text-white">{title}</h2>
      {children}
    </div>
  );
}

function List({ title, items }: { title: string; items?: string[] }) {
  if (!items?.length) return null;
  return (
    <div className="mt-4">
      <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">{title}</div>
      <ul className="space-y-2 text-sm text-slate-300">
        {items.map((item, index) => (
          <li key={index} className="rounded-md border border-slate-800/80 bg-slate-950/58 px-3 py-2">
            {item}
          </li>
        ))}
      </ul>
    </div>
  );
}
