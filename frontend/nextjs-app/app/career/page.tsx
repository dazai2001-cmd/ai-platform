"use client";

import type { ReactNode } from "react";
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  BookmarkPlus,
  BriefcaseBusiness,
  CheckCircle2,
  ChevronDown,
  Download,
  ExternalLink,
  FileText,
  FolderOpen,
  Gauge,
  Link as LinkIcon,
  Loader2,
  Search,
  Settings,
  Square,
  Sparkles,
  Trash2,
  XCircle,
} from "lucide-react";
import CvProfileImport from "@/components/career/CvProfileImport";
import { api, type CareerProfileImportResult } from "@/lib/api";

type CareerJob = {
  id: string;
  title: string;
  company: string;
  location: string;
  url: string;
  description: string;
  source: string;
  status: string;
  fit_score?: number | null;
  decision?: string;
  analysis?: any;
  applied_at?: number | null;
  created_at?: number;
  updated_at?: number;
};

const defaultPreferences = {
  roles: "",
  locations: "",
  remote: "any",
  industries: "",
  must_have: "",
  avoid: "",
  match_mode: "both",
};

type CareerAnalysis = {
  fit_score?: number | null;
  summary?: string;
  matched_skills?: string[];
  missing_or_weak_signals?: string[];
};

type CareerTab = "found" | "matches" | "saved" | "applied" | "skipped";
type MatchSort = "score" | "newest" | "oldest";
type WorkModeFilter = "all" | "remote" | "hybrid" | "onsite";

type ScoreBatchProgress = {
  id: string;
  total: number;
  completed: number;
  failed: number;
  cancelled: number;
  remaining: number;
  processed: number;
  progress: number;
  current_job?: { id: string; title: string } | null;
  status: "queued" | "running" | "completed" | "cancelled";
};

const careerTabs: { id: CareerTab; label: string }[] = [
  { id: "found", label: "Found" },
  { id: "matches", label: "Matches" },
  { id: "saved", label: "Saved" },
  { id: "applied", label: "Applied" },
  { id: "skipped", label: "Skipped" },
];
const workModeFilters: { id: WorkModeFilter; label: string }[] = [
  { id: "all", label: "All" },
  { id: "remote", label: "Remote" },
  { id: "hybrid", label: "Hybrid" },
  { id: "onsite", label: "On-site" },
];
const MIN_MATCH_SCORE = 70;

export default function CareerPage() {
  const [cvText, setCvText] = useState("");
  const [jobDescription, setJobDescription] = useState("");
  const [careerModel, setCareerModel] = useState("");
  const [preferences, setPreferences] = useState<Record<string, string>>(defaultPreferences);
  const [preferencesReady, setPreferencesReady] = useState(false);
  const [profileReady, setProfileReady] = useState(false);
  const [jobs, setJobs] = useState<CareerJob[]>([]);
  const [jobUrl, setJobUrl] = useState("");
  const [loadingAction, setLoadingAction] = useState<"" | "analysis">("");
  const [jobAction, setJobAction] = useState("");
  const [activeTab, setActiveTab] = useState<CareerTab>("matches");
  const [result, setResult] = useState<any>(null);
  const [resultJobTitle, setResultJobTitle] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [scoreBatch, setScoreBatch] = useState<ScoreBatchProgress | null>(null);
  const [matchSort, setMatchSort] = useState<MatchSort>("score");
  const [workModeFilter, setWorkModeFilter] = useState<WorkModeFilter>("all");
  const [expandedJobIds, setExpandedJobIds] = useState<string[]>([]);
  const [cvImporting, setCvImporting] = useState(false);
  const lastSyncedCvRef = useRef<string | null>(null);
  const profileWriteQueueRef = useRef<Promise<void>>(Promise.resolve());
  const profileWriteGenerationRef = useRef(0);
  const loading = Boolean(loadingAction || jobAction || cvImporting);

  useEffect(() => {
    const tab = new URLSearchParams(window.location.search).get("tab") as CareerTab | null;
    if (tab && careerTabs.some((item) => item.id === tab)) {
      setActiveTab(tab);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      api.modelSettings(),
      api.careerPreferences(),
      api.careerProfile(),
      api.careerJobs(),
      api.currentCareerScoreBatch(),
    ])
      .then(([settings, prefs, profile, savedJobs, currentBatch]) => {
        const localPrefs = readLocalCareerPreferences();
        if (!cancelled) setCareerModel(settings.task_models?.career || "");
        if (!cancelled) setPreferences({ ...defaultPreferences, ...(prefs || {}), ...localPrefs });
        if (!cancelled) {
          lastSyncedCvRef.current = profile?.cv_text || "";
          setCvText(profile?.cv_text || readLocalCareerProfile());
        }
        if (!cancelled) setJobs(savedJobs || []);
        if (!cancelled) setScoreBatch(currentBatch || null);
        if (!cancelled) setPreferencesReady(true);
        if (!cancelled) setProfileReady(true);
      })
      .catch(() => {
        if (!cancelled) setCareerModel("");
        if (!cancelled) {
          lastSyncedCvRef.current = null;
          setPreferences({ ...defaultPreferences, ...readLocalCareerPreferences() });
          setCvText(readLocalCareerProfile());
          setPreferencesReady(true);
          setProfileReady(true);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!preferencesReady) return;
    window.localStorage.setItem("career-search-criteria", JSON.stringify(preferences));
  }, [preferences, preferencesReady]);

  useEffect(() => {
    if (!profileReady) return;
    const cleanProfile = cvText.trim();
    if (cleanProfile) {
      window.localStorage.setItem("career-profile", cvText);
    } else {
      window.localStorage.removeItem("career-profile");
    }
    const profileValue = cleanProfile ? cvText : "";
    if (lastSyncedCvRef.current === profileValue) return;
    const generation = ++profileWriteGenerationRef.current;
    const timeout = window.setTimeout(() => {
      const write = async () => {
        if (generation !== profileWriteGenerationRef.current) return;
        try {
          const profile = cleanProfile
            ? await api.updateCareerProfile(cvText)
            : await api.deleteCareerProfile();
          if (generation === profileWriteGenerationRef.current) {
            lastSyncedCvRef.current = profile?.cv_text ?? profileValue;
          }
        } catch {
          // Preserve the local draft. A later edit/import can retry safely.
        }
      };
      profileWriteQueueRef.current = profileWriteQueueRef.current
        .catch(() => undefined)
        .then(write);
    }, 800);
    return () => window.clearTimeout(timeout);
  }, [cvText, profileReady]);

  const settlePendingProfileWrites = async () => {
    // Invalidate debounced writes that have not started, then wait for any
    // request already in flight so an import/delete is guaranteed to run last.
    profileWriteGenerationRef.current += 1;
    await profileWriteQueueRef.current.catch(() => undefined);
  };

  useEffect(() => {
    if (!scoreBatch || !["queued", "running"].includes(scoreBatch.status)) return;
    let cancelled = false;

    const refreshBatch = async () => {
      try {
        const [batch, refreshedJobs] = await Promise.all([
          api.careerScoreBatch(scoreBatch.id),
          api.careerJobs(),
        ]);
        if (!cancelled) {
          setScoreBatch(batch);
          setJobs(refreshedJobs || []);
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to refresh scoring progress");
      }
    };

    const interval = window.setInterval(refreshBatch, 2000);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [scoreBatch?.id, scoreBatch?.status]);

  const savePreferences = async () => {
    setJobAction("preferences");
    setError("");
    setNotice("");
    try {
      setPreferences(await api.updateCareerPreferences(preferences));
      setNotice("Search criteria saved.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save preferences");
    } finally {
      setJobAction("");
    }
  };

  const searchJobs = async () => {
    setJobAction("search");
    setError("");
    setNotice("");
    try {
      await api.updateCareerPreferences(preferences);
      setActiveTab("found");
      const response = await api.searchCareerJobsStream(cvText.trim() || undefined, 50);
      await readCareerSearchStream(response);
      const refreshedJobs = await api.careerJobs();
      setJobs(refreshedJobs);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to search jobs");
    } finally {
      setJobAction("");
    }
  };

  const readCareerSearchStream = async (response: Response) => {
    const reader = response.body?.getReader();
    if (!reader) throw new Error("No search stream returned.");

    const decoder = new TextDecoder();
    let buffer = "";
    let savedCount = 0;
    let scoredCount = 0;
    let rejectedLowScore = 0;
    let query = "jobs";
    let matchMode = preferences.match_mode;
    let searchedSources: string[] = [];
    let skippedSources: string[] = [];

    const updateNotice = (done = false) => {
      const searched = searchedSources.length ? ` Searched: ${searchedSources.join(", ")}.` : "";
      const skipped = skippedSources.length ? ` Skipped: ${skippedSources.join(", ")}.` : "";
      const matchedCount = Math.max(savedCount - rejectedLowScore, 0);
      const scored = scoredCount ? ` ${scoredCount} scored; ${matchedCount} are ${MIN_MATCH_SCORE}+ Matches.` : "";
      const rejected = rejectedLowScore ? ` ${rejectedLowScore} below ${MIN_MATCH_SCORE}/100 kept in Found.` : "";
      const empty = done && savedCount === 0 ? " Try broader roles, fewer avoid terms, or Remote as the work mode." : "";
      setNotice(`Found ${savedCount} new jobs for "${query}" using ${matchModeLabel(matchMode)}.${searched}${skipped}${scored}${rejected}${empty}`);
    };

    const handleEvent = (event: any) => {
      if (event.event === "started") {
        query = event.query || query;
        matchMode = event.match_mode || matchMode;
        searchedSources = event.searched_sources || [];
        skippedSources = event.skipped_sources || [];
        setNotice(`Searching "${query}"...${searchedSources.length ? ` Sources: ${searchedSources.join(", ")}.` : ""}`);
        return;
      }

      if ((event.event === "found" || event.event === "scored") && event.job && event.accepted !== false) {
        savedCount = event.saved_count ?? savedCount + 1;
        if (event.event === "scored") scoredCount = event.scored_count ?? scoredCount + 1;
        setJobs((current) => mergeJobs([event.job], current));
        if (event.event === "scored") {
          const score = event.job.fit_score ?? "?";
          setNotice(`Scored "${event.job.title || "job"}": ${score}/100. Added to Matches. ${savedCount} saved, ${scoredCount} scored so far.`);
        } else {
          updateNotice();
        }
        return;
      }

      if (event.event === "scored" && event.accepted === false) {
        scoredCount = event.scored_count ?? scoredCount + 1;
        savedCount = event.saved_count ?? savedCount + 1;
        rejectedLowScore = event.rejected_low_score ?? rejectedLowScore + 1;
        const score = event.job?.fit_score ?? "?";
        if (event.job) setJobs((current) => mergeJobs([event.job], current));
        setNotice(`Scored "${event.job?.title || "job"}": ${score}/100. Kept in Found; below ${MIN_MATCH_SCORE}/100 so it is not in Matches. ${scoredCount} scored so far.`);
        return;
      }

      if (event.event === "error") {
        setError(event.error || "Search stream error");
        return;
      }

      if (event.event === "done") {
        savedCount = event.count ?? savedCount;
        scoredCount = event.scored_count ?? scoredCount;
        rejectedLowScore = event.rejected_low_score ?? rejectedLowScore;
        searchedSources = event.searched_sources || searchedSources;
        skippedSources = event.skipped_sources || skippedSources;
        updateNotice(true);
      }
    };

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";
      for (const line of lines) {
        if (!line.trim()) continue;
        handleEvent(JSON.parse(line));
      }
    }
    if (buffer.trim()) handleEvent(JSON.parse(buffer));
  };

  const importJobUrl = async () => {
    if (!jobUrl.trim()) return;
    setJobAction("import-url");
    setError("");
    setNotice("");
    try {
      const job = await api.importCareerJobUrl(jobUrl.trim(), cvText.trim() || undefined);
      setJobs((current) => [job, ...current]);
      setJobDescription(job.description);
      setJobUrl("");
      setActiveTab(job.status === "saved" ? "saved" : "matches");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to import job URL");
    } finally {
      setJobAction("");
    }
  };

  const saveCurrentJob = async () => {
    if (!jobDescription.trim()) return;
    setJobAction("save-job");
    setError("");
    setNotice("");
    try {
      const job = await api.saveCareerJob({
        description: jobDescription,
        cv_text: cvText.trim() || undefined,
        source: "manual",
      });
      setJobs((current) => [job, ...current]);
      setActiveTab(job.status === "saved" ? "saved" : "matches");
      setNotice("Job saved to the tracker.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save job");
    } finally {
      setJobAction("");
    }
  };

  const scoreSavedJob = async (job: CareerJob) => {
    if (!cvText.trim()) {
      setError("Paste your CV/profile before scoring a saved job.");
      return;
    }
    setJobAction(job.id);
    setError("");
    setNotice("");
    try {
      const scored = await api.scoreCareerJob(job.id, cvText);
      setJobs((current) => current.map((item) => (item.id === scored.id ? scored : item)));
      setNotice(`Scored "${scored.title || "job"}": ${scored.fit_score ?? "?"}/100.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to score saved job");
    } finally {
      setJobAction("");
    }
  };

  const scoreAllFoundJobs = async () => {
    if (!cvText.trim()) {
      setError("Paste your CV/profile before scoring jobs.");
      return;
    }
    const jobIds = jobs
      .filter((job) => jobBelongsToTab(job, "found") && typeof job.fit_score !== "number")
      .map((job) => job.id);
    if (!jobIds.length) return;

    setJobAction("score-all");
    setError("");
    setNotice("");
    try {
      const batch = await api.startCareerScoreBatch(cvText, jobIds);
      setScoreBatch(batch);
      setNotice(`${batch.total} unique Found jobs queued. You can leave this page while they score.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to start background scoring");
    } finally {
      setJobAction("");
    }
  };

  const clearFoundJobs = async () => {
    const foundIds = jobs.filter((job) => jobBelongsToTab(job, "found")).map((job) => job.id);
    if (!foundIds.length) return;
    const label = foundIds.length === 1 ? "1 Found job" : `${foundIds.length} Found jobs`;
    if (!window.confirm(`Clear ${label}? Matches, saved, applied, and skipped jobs will stay untouched.`)) return;

    setJobAction("clear-found");
    setError("");
    setNotice("");
    try {
      await Promise.all(foundIds.map((jobId) => api.deleteCareerJob(jobId)));
      setJobs((current) => current.filter((job) => !foundIds.includes(job.id)));
      setActiveTab("found");
      setNotice(`Cleared ${label}.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to clear Found jobs");
    } finally {
      setJobAction("");
    }
  };

  const deleteCareerProfile = async () => {
    if (!cvText.trim()) return;
    if (!window.confirm("Delete your saved CV/profile? Your jobs and search criteria will stay untouched.")) return;

    setJobAction("delete-profile");
    setError("");
    setNotice("");
    try {
      await settlePendingProfileWrites();
      await api.deleteCareerProfile();
      lastSyncedCvRef.current = "";
      window.localStorage.removeItem("career-profile");
      setCvText("");
      setNotice("Saved CV/profile deleted.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete saved profile");
    } finally {
      setJobAction("");
    }
  };

  const importCareerProfile = (profile: CareerProfileImportResult) => {
    lastSyncedCvRef.current = profile.cv_text;
    setCvText(profile.cv_text);
    setError("");
  };

  const stopScoreAll = () => {
    if (!scoreBatch || !["queued", "running"].includes(scoreBatch.status)) return;
    setJobAction("cancel-score-all");
    setError("");
    api.cancelCareerScoreBatch(scoreBatch.id)
      .then((batch) => {
        setScoreBatch(batch);
        setNotice("Background scoring stopped. The current model call may finish safely.");
      })
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to stop background scoring"))
      .finally(() => setJobAction(""));
  };

  const toggleJobDetails = (jobId: string) => {
    setExpandedJobIds((current) =>
      current.includes(jobId) ? current.filter((id) => id !== jobId) : [...current, jobId],
    );
  };

  const updateJobStatus = async (jobId: string, status: string) => {
    const tabBeforeUpdate = activeTab;
    setJobAction(`${jobId}:${status}`);
    setError("");
    setNotice("");
    try {
      const updated = await api.updateCareerJobStatus(jobId, status);
      setJobs((current) => current.map((job) => (job.id === updated.id ? updated : job)));
      setActiveTab(tabBeforeUpdate);
      setNotice(`Marked "${updated.title || "job"}" as ${statusLabel(updated.status).toLowerCase()}.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to update job status");
      setActiveTab(tabBeforeUpdate);
    } finally {
      setJobAction("");
    }
  };

  const openApplyLink = async (job: CareerJob) => {
    if (!job.url) {
      setError("This saved job does not have an application link yet.");
      return;
    }
    window.open(job.url, "_blank", "noopener,noreferrer");
    await updateJobStatus(job.id, "opened");
  };

  const removeJob = async (jobId: string) => {
    setJobAction(jobId);
    setError("");
    setNotice("");
    try {
      await api.deleteCareerJob(jobId);
      setJobs((current) => current.filter((job) => job.id !== jobId));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete job");
    } finally {
      setJobAction("");
    }
  };

  const scoreFit = async () => {
    setLoadingAction("analysis");
    setError("");
    setNotice("");
    setResultJobTitle("");
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

  const generateForJob = async (job: CareerJob) => {
    if (!cvText.trim()) {
      setError("Paste your CV/profile before generating an application pack.");
      return;
    }
    setJobAction(`pack:${job.id}`);
    setError("");
    setNotice("");
    setResult(null);
    setResultJobTitle([job.title, job.company].filter(Boolean).join(" at "));
    setJobDescription(job.description);
    try {
      const pack = await api.generateCareerMatchPack(job.id, cvText);
      setResult(pack);
      if (pack.model) setCareerModel(pack.model);
      setNotice(`Application pack generated for "${job.title}".`);
      window.setTimeout(() => document.getElementById("career-pack-output")?.scrollIntoView({ behavior: "smooth" }), 50);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to generate application pack");
    } finally {
      setJobAction("");
    }
  };

  const downloadPack = () => {
    if (!result) return;
    const analysis = getCareerAnalysis(result);
    const tailoredCv = getTailoredCv(result);
    const coverLetter = getCoverLetter(result);
    const lines = [
      "# Career Agent Output",
      "",
      "## Fit Analysis",
      "",
      `Score: ${analysis?.fit_score ?? "Not returned"}/100`,
      "",
      analysis?.summary || "",
      "",
      "## Tailored CV",
      "",
      tailoredCv?.headline || "",
      "",
      tailoredCv?.professional_summary || "",
      "",
      ...(tailoredCv?.tailored_bullets || []).map((item: string) => `- ${item}`),
      "",
      "## Cover Letter",
      "",
      coverLetter?.cover_letter || "",
    ];
    const blob = new Blob([lines.join("\n")], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "career-agent-output.md";
    link.click();
    URL.revokeObjectURL(url);
  };

  const analysis = getCareerAnalysis(result);
  const tailoredCv = getTailoredCv(result);
  const coverLetter = getCoverLetter(result);
  const resultWarning = getResultWarning(result);
  const foundJobs = jobs.filter((job) => jobBelongsToTab(job, "found"));
  const tabCounts = Object.fromEntries(
    careerTabs.map((tab) => [tab.id, jobs.filter((job) => jobBelongsToTab(job, tab.id)).length]),
  ) as Record<CareerTab, number>;
  const visibleJobs = jobs.filter((job) => jobBelongsToTab(job, activeTab));
  const displayedJobs = activeTab === "matches"
    ? visibleJobs
        .filter((job) => workModeFilter === "all" || jobWorkMode(job) === workModeFilter)
        .sort((left, right) => compareMatchJobs(left, right, matchSort))
    : visibleJobs;
  const unscoredFoundJobs = foundJobs.filter((job) => typeof job.fit_score !== "number");
  const scoreBatchActive = Boolean(scoreBatch && ["queued", "running"].includes(scoreBatch.status));
  const scoreBatchPercent = scoreBatch?.progress ?? 0;

  return (
    <div className="min-h-screen text-ink">
      <section className="border-b border-line-soft bg-panel px-5 py-5">
        <div className="flex items-center gap-3">
          <div className="grid h-10 w-10 place-items-center rounded-md border border-brand/20 bg-brand/10 text-brand-ink">
            <BriefcaseBusiness size={20} />
          </div>
          <div>
            <h1 className="text-lg font-semibold text-ink">Career Agent</h1>
            <p className="text-sm text-muted">Job search, CV matching, and application drafts using the selected model</p>
          </div>
        </div>
      </section>

      <main className="grid gap-0 lg:grid-cols-[420px_1fr]">
        <section className="border-b border-line-soft bg-panel p-5 lg:min-h-[calc(100vh-82px)] lg:border-b-0 lg:border-r">
          <div className="app-panel mb-4 rounded-md p-3">
            <div className="mb-1 flex items-center justify-between gap-3">
              <span className="text-xs font-semibold uppercase tracking-wide text-muted">Agent Model</span>
              <Link
                href="/settings"
                className="inline-flex items-center gap-1.5 rounded px-1.5 py-1 text-xs text-muted transition hover:bg-soft hover:text-analytic-hover"
              >
                <Settings size={13} />
                Settings
              </Link>
            </div>
            <div className="truncate text-sm font-medium text-ink">{careerModel || "Loading..."}</div>
          </div>

          <div className="app-panel mb-4 rounded-md p-3">
            <div className="mb-3 flex items-center justify-between gap-3">
              <div>
                <div className="text-xs font-semibold uppercase tracking-wide text-muted">Job Search Criteria</div>
                <div className="text-xs text-muted-soft">Used to find and rank roles later.</div>
              </div>
              <div className="flex gap-2">
                <button
                  onClick={savePreferences}
                  disabled={loading}
                  className="rounded-md border border-line px-2.5 py-1.5 text-xs text-ink-subtle transition hover:border-brand hover:text-analytic-hover disabled:opacity-50"
                >
                  {jobAction === "preferences" ? "Saving..." : "Save"}
                </button>
                <button
                  onClick={searchJobs}
                  disabled={loading}
                  className="inline-flex items-center gap-1.5 rounded-md bg-brand px-2.5 py-1.5 text-xs font-medium text-white transition hover:bg-brand-hover disabled:opacity-50"
                >
                  {jobAction === "search" ? <Loader2 size={13} className="animate-spin" /> : <Search size={13} />}
                  Search
                </button>
              </div>
            </div>
            <div className="grid gap-2">
              <input
                className="app-input rounded-md px-3 py-2 text-sm placeholder:text-muted-soft"
                placeholder="Roles: AI Engineer, ML Intern..."
                value={preferences.roles}
                onChange={(e) => setPreferences((current) => ({ ...current, roles: e.target.value }))}
              />
              <input
                className="app-input rounded-md px-3 py-2 text-sm placeholder:text-muted-soft"
                placeholder="Locations: remote, London..."
                value={preferences.locations}
                onChange={(e) => setPreferences((current) => ({ ...current, locations: e.target.value }))}
              />
              <select
                className="app-input rounded-md px-3 py-2 text-sm"
                value={preferences.remote}
                onChange={(e) => setPreferences((current) => ({ ...current, remote: e.target.value }))}
              >
                <option value="any">Any work mode</option>
                <option value="remote">Remote</option>
                <option value="hybrid">Hybrid</option>
                <option value="onsite">On-site</option>
              </select>
              <select
                className="app-input rounded-md px-3 py-2 text-sm"
                value={preferences.match_mode}
                onChange={(e) => setPreferences((current) => ({ ...current, match_mode: e.target.value }))}
              >
                <option value="both">Profile + criteria</option>
                <option value="profile">Profile only</option>
                <option value="criteria">Criteria only</option>
              </select>
              <input
                className="app-input rounded-md px-3 py-2 text-sm placeholder:text-muted-soft"
                placeholder="Must-have skills"
                value={preferences.must_have}
                onChange={(e) => setPreferences((current) => ({ ...current, must_have: e.target.value }))}
              />
              <input
                className="app-input rounded-md px-3 py-2 text-sm placeholder:text-muted-soft"
                placeholder="Avoid: unpaid, senior..."
                value={preferences.avoid}
                onChange={(e) => setPreferences((current) => ({ ...current, avoid: e.target.value }))}
              />
            </div>
          </div>

          <div className="app-panel mb-4 rounded-md p-3">
            <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">Import Job URL</div>
            <input
              className="app-input mb-2 w-full rounded-md px-3 py-2 text-sm placeholder:text-muted-soft"
              placeholder="https://jobs.lever.co/..."
              value={jobUrl}
              onChange={(e) => setJobUrl(e.target.value)}
            />
            <button
              onClick={importJobUrl}
              disabled={loading || !jobUrl.trim()}
              className="flex w-full items-center justify-center gap-2 rounded-md border border-line bg-panel/82 px-3 py-2 text-sm font-medium text-ink transition hover:border-brand hover:text-analytic-hover disabled:opacity-50"
            >
              {jobAction === "import-url" ? <Loader2 size={15} className="animate-spin" /> : <LinkIcon size={15} />}
              Import & Score
            </button>
          </div>

          <div className="mb-2 flex items-center justify-between gap-3">
            <label className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-muted">
              <FileText size={14} /> CV / Profile
            </label>
            <button
              onClick={deleteCareerProfile}
              disabled={loading || !cvText.trim()}
              className="inline-flex items-center gap-1.5 rounded-md border border-danger/25 bg-danger/10 px-2.5 py-1 text-xs font-medium text-danger-ink transition hover:border-danger hover:bg-danger-hover/15 disabled:opacity-50"
            >
              {jobAction === "delete-profile" ? <Loader2 size={12} className="animate-spin" /> : <Trash2 size={12} />}
              Delete Profile
            </button>
          </div>
          <CvProfileImport
            disabled={Boolean(loadingAction || jobAction)}
            hasProfile={Boolean(cvText.trim())}
            beforeImport={settlePendingProfileWrites}
            onBusyChange={setCvImporting}
            onImported={importCareerProfile}
          />
          <textarea
            value={cvText}
            onChange={(e) => setCvText(e.target.value)}
            aria-label="CV or profile text"
            placeholder="Upload a PDF or Word file above, or paste your CV/profile text here."
            className="app-input h-56 w-full resize-none rounded-md px-3 py-2 text-sm"
          />

          <label className="mb-2 mt-4 block text-xs font-semibold uppercase tracking-wide text-muted">
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
              className="flex items-center justify-center gap-2 rounded-md border border-line bg-panel/82 px-3 py-2 text-sm font-medium text-ink transition duration-150 hover:border-brand hover:text-analytic-hover disabled:cursor-not-allowed disabled:opacity-50"
            >
              {loadingAction === "analysis" ? <Loader2 className="animate-spin" size={16} /> : <Gauge size={16} />}
              Score Fit
            </button>
            <button
              onClick={saveCurrentJob}
              disabled={loading || !jobDescription.trim()}
              className="flex items-center justify-center gap-2 rounded-md border border-line bg-panel/82 px-3 py-2 text-sm font-medium text-ink transition duration-150 hover:border-brand hover:text-analytic-hover disabled:cursor-not-allowed disabled:opacity-50"
            >
              {jobAction === "save-job" ? <Loader2 className="animate-spin" size={16} /> : <BookmarkPlus size={16} />}
              Save Job
            </button>
          </div>

          {notice && (
            <div className="mt-4 rounded-md border border-brand/25 bg-brand/10 p-3 text-sm text-brand-ink">
              {notice}
            </div>
          )}
          {error && <div className="mt-4 rounded-md border border-danger/30 bg-danger/10 p-3 text-sm text-danger-ink">{error}</div>}
        </section>

        <section className="space-y-5 p-5">
          <Panel title="Application Tracker">
            <div className="mb-4 flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
              <div className="flex flex-wrap gap-2">
                {careerTabs.map((tab) => (
                  <button
                    key={tab.id}
                    onClick={() => setActiveTab(tab.id)}
                    className={`rounded-md border px-3 py-1.5 text-xs font-medium transition ${
                      activeTab === tab.id
                        ? "border-brand/50 bg-brand/10 text-brand-ink"
                        : "border-line-soft bg-canvas/40 text-muted hover:border-line-strong hover:text-ink"
                    }`}
                  >
                    {tab.label} {tabCounts[tab.id] ? `(${tabCounts[tab.id]})` : ""}
                  </button>
                ))}
              </div>
              <div className="flex flex-wrap gap-2">
                <button
                  onClick={scoreBatchActive ? stopScoreAll : scoreAllFoundJobs}
                  disabled={
                    scoreBatchActive
                      ? jobAction === "cancel-score-all"
                      : loading || !cvText.trim() || unscoredFoundJobs.length === 0
                  }
                  className="inline-flex items-center justify-center gap-1.5 rounded-md border border-line bg-canvas/50 px-3 py-1.5 text-xs font-medium text-ink-subtle transition hover:border-brand hover:text-analytic-hover disabled:opacity-50"
                >
                  {scoreBatchActive ? <Square size={12} /> : <Gauge size={13} />}
                  {scoreBatchActive
                    ? jobAction === "cancel-score-all" ? "Stopping..." : "Stop after current"
                    : `Score all Found${unscoredFoundJobs.length ? ` (${unscoredFoundJobs.length})` : ""}`}
                </button>
                <button
                  onClick={clearFoundJobs}
                  disabled={loading || scoreBatchActive || foundJobs.length === 0}
                  className="inline-flex items-center justify-center gap-1.5 rounded-md border border-danger/30 bg-danger/10 px-3 py-1.5 text-xs font-medium text-danger-ink transition hover:border-danger hover:bg-danger-hover/15 disabled:opacity-50"
                >
                  {jobAction === "clear-found" ? <Loader2 className="animate-spin" size={13} /> : <Trash2 size={13} />}
                  Clear Found{foundJobs.length ? ` (${foundJobs.length})` : ""}
                </button>
              </div>
            </div>

            {scoreBatch && (
              <div className="mb-4 rounded-md border border-brand/25 bg-brand/10 p-3" aria-live="polite">
                <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
                  <span className="font-medium text-brand-ink">
                    {scoreBatch.status === "completed"
                      ? "Background scoring complete"
                      : scoreBatch.status === "cancelled"
                        ? "Background scoring stopped"
                        : scoreBatch.status === "queued"
                          ? "Background scoring queued"
                          : `Scoring ${Math.min(scoreBatch.processed + 1, scoreBatch.total)} of ${scoreBatch.total}`}
                  </span>
                  <span className="text-muted">
                    {scoreBatch.processed}/{scoreBatch.total} processed / {scoreBatch.completed} scored
                    {scoreBatch.failed ? ` / ${scoreBatch.failed} failed` : ""}
                  </span>
                </div>
                <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-panel">
                  <div
                    className="h-full rounded-full bg-brand transition-[width] duration-300"
                    style={{ width: `${scoreBatchPercent}%` }}
                  />
                </div>
                {scoreBatch.current_job?.title && (
                  <div className="mt-2 truncate text-xs text-ink-subtle">Currently scoring: {scoreBatch.current_job.title}</div>
                )}
              </div>
            )}

            {activeTab === "matches" && (
              <div className="mb-4 flex flex-col gap-3 border-b border-line-soft/80 pb-4 xl:flex-row xl:items-center xl:justify-between">
                <div className="flex flex-wrap gap-1" aria-label="Work mode filter">
                  {workModeFilters.map((mode) => (
                    <button
                      key={mode.id}
                      onClick={() => setWorkModeFilter(mode.id)}
                      aria-pressed={workModeFilter === mode.id}
                      className={`rounded-md border px-2.5 py-1.5 text-xs font-medium transition ${
                        workModeFilter === mode.id
                          ? "border-brand/50 bg-brand/10 text-brand-ink"
                          : "border-line-soft text-muted hover:border-line-strong hover:text-ink"
                      }`}
                    >
                      {mode.label}
                    </button>
                  ))}
                </div>
                <select
                  aria-label="Sort matches"
                  value={matchSort}
                  onChange={(event) => setMatchSort(event.target.value as MatchSort)}
                  className="app-input min-w-40 rounded-md px-2.5 py-1.5 text-xs"
                >
                  <option value="score">Highest score</option>
                  <option value="newest">Newest first</option>
                  <option value="oldest">Oldest first</option>
                </select>
              </div>
            )}

            {jobs.length === 0 ? (
              <div className="rounded-md border border-dashed border-line-soft px-4 py-8 text-center text-sm text-muted">
                Import a job URL or save the pasted job description to build a match list.
              </div>
            ) : displayedJobs.length === 0 ? (
              <div className="rounded-md border border-dashed border-line-soft px-4 py-8 text-center text-sm text-muted">
                No jobs match this {careerTabs.find((tab) => tab.id === activeTab)?.label.toLowerCase()} view.
              </div>
            ) : (
              <div className="space-y-3">
                {displayedJobs.map((job) => (
                  <div key={job.id} className="rounded-md border border-line-soft bg-panel p-3">
                    <div className="mb-3 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                      <div className="min-w-0">
                        <div className="truncate text-sm font-semibold text-ink">{job.title}</div>
                        <div className="mt-1 text-xs text-muted">
                          {[job.company, job.location, job.source].filter(Boolean).join(" / ") || "Saved job"}
                        </div>
                      </div>
                      <div className="flex shrink-0 items-center gap-2">
                        {job.fit_score !== null && job.fit_score !== undefined ? (
                          <span className="rounded-md border border-brand/25 bg-brand/10 px-2 py-1 text-xs font-semibold text-brand-ink">
                            {job.fit_score}/100
                          </span>
                        ) : (
                          <span className="rounded-md border border-line px-2 py-1 text-xs text-muted">
                            Unscored
                          </span>
                        )}
                        {activeTab === "matches" && jobWorkMode(job) !== "unknown" && (
                          <span className="rounded-md border border-line px-2 py-1 text-xs text-muted">
                            {workModeLabel(jobWorkMode(job))}
                          </span>
                        )}
                        {job.decision && (
                          <span className="rounded-md border border-line px-2 py-1 text-xs text-muted">
                            {job.decision}
                          </span>
                        )}
                        <span className="rounded-md border border-line px-2 py-1 text-xs text-muted">
                          {statusLabel(job.status)}
                        </span>
                      </div>
                    </div>

                    {expandedJobIds.includes(job.id) && <JobMatchDetails analysis={job.analysis} />}

                    {job.status === "opened" && (
                      <div className="mb-3 rounded-md border border-brand/20 bg-brand/10 p-3 text-sm text-ink-subtle">
                        <div className="mb-2 font-medium text-brand-ink">Did you apply?</div>
                        <div className="flex flex-wrap gap-2">
                          <StatusButton
                            icon={<CheckCircle2 size={13} />}
                            label="Applied"
                            loading={jobAction === `${job.id}:applied`}
                            onClick={() => updateJobStatus(job.id, "applied")}
                          />
                          <StatusButton
                            icon={<FolderOpen size={13} />}
                            label="Save for later"
                            loading={jobAction === `${job.id}:saved`}
                            onClick={() => updateJobStatus(job.id, "saved")}
                          />
                          <StatusButton
                            icon={<XCircle size={13} />}
                            label="Skipped"
                            loading={jobAction === `${job.id}:skipped`}
                            onClick={() => updateJobStatus(job.id, "skipped")}
                          />
                        </div>
                      </div>
                    )}

                    <div className="flex flex-wrap gap-2">
                      <button
                        onClick={() => setJobDescription(job.description)}
                        className="rounded-md border border-line px-2.5 py-1.5 text-xs text-ink-subtle transition hover:border-brand hover:text-analytic-hover"
                      >
                        Use
                      </button>
                      {job.analysis && (
                        <button
                          onClick={() => toggleJobDetails(job.id)}
                          aria-expanded={expandedJobIds.includes(job.id)}
                          className="inline-flex items-center gap-1.5 rounded-md border border-line px-2.5 py-1.5 text-xs text-ink-subtle transition hover:border-brand hover:text-analytic-hover"
                        >
                          <ChevronDown
                            size={13}
                            className={`transition-transform ${expandedJobIds.includes(job.id) ? "rotate-180" : ""}`}
                          />
                          Details
                        </button>
                      )}
                      {activeTab === "matches" && (
                        <button
                          onClick={() => generateForJob(job)}
                          disabled={loading || !cvText.trim()}
                          className="inline-flex items-center gap-1.5 rounded-md border border-brand/30 bg-brand/10 px-2.5 py-1.5 text-xs font-medium text-brand-ink transition hover:border-brand hover:bg-brand-hover/15 disabled:opacity-50"
                        >
                          {jobAction === `pack:${job.id}` ? <Loader2 size={13} className="animate-spin" /> : <Sparkles size={13} />}
                          Generate Pack
                        </button>
                      )}
                      <button
                        onClick={() => scoreSavedJob(job)}
                        disabled={loading || !cvText.trim()}
                        className="inline-flex items-center gap-1.5 rounded-md border border-line px-2.5 py-1.5 text-xs text-ink-subtle transition hover:border-brand hover:text-analytic-hover disabled:opacity-50"
                      >
                        {jobAction === job.id ? <Loader2 size={13} className="animate-spin" /> : <Gauge size={13} />}
                        Score
                      </button>
                      {job.url && (
                        <button
                          onClick={() => openApplyLink(job)}
                          disabled={loading}
                          className="inline-flex items-center gap-1.5 rounded-md border border-line px-2.5 py-1.5 text-xs text-ink-subtle transition hover:border-brand hover:text-analytic-hover"
                        >
                          <ExternalLink size={13} />
                          Apply
                        </button>
                      )}
                      <button
                        onClick={() => updateJobStatus(job.id, "applied")}
                        disabled={loading}
                        className="inline-flex items-center gap-1.5 rounded-md border border-line px-2.5 py-1.5 text-xs text-ink-subtle transition hover:border-success hover:text-success-ink disabled:opacity-50"
                      >
                        <CheckCircle2 size={13} />
                        Applied
                      </button>
                      <button
                        onClick={() => updateJobStatus(job.id, "saved")}
                        disabled={loading}
                        className="inline-flex items-center gap-1.5 rounded-md border border-line px-2.5 py-1.5 text-xs text-ink-subtle transition hover:border-brand hover:text-analytic-hover disabled:opacity-50"
                      >
                        <FolderOpen size={13} />
                        Save
                      </button>
                      <button
                        onClick={() => updateJobStatus(job.id, "skipped")}
                        disabled={loading}
                        className="inline-flex items-center gap-1.5 rounded-md border border-line px-2.5 py-1.5 text-xs text-muted transition hover:border-warning hover:text-warning-ink disabled:opacity-50"
                      >
                        <XCircle size={13} />
                        Skip
                      </button>
                      <button
                        onClick={() => removeJob(job.id)}
                        disabled={loading}
                        className="inline-flex items-center gap-1.5 rounded-md border border-line px-2.5 py-1.5 text-xs text-muted transition hover:border-danger hover:text-danger-ink disabled:opacity-50"
                      >
                        <Trash2 size={13} />
                        Delete
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </Panel>

          {!result ? (
            <div className="app-panel soft-fade-in rounded-md border-dashed px-4 py-16 text-center text-sm text-muted">
              Paste a CV and job description to generate a fit score, tailored CV points, and a cover letter.
            </div>
          ) : (
            <div id="career-pack-output" className="space-y-4">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  <div className="text-xs font-semibold uppercase tracking-wide text-muted">Application pack</div>
                  {resultJobTitle && <div className="mt-1 text-sm font-medium text-ink">{resultJobTitle}</div>}
                </div>
                <button
                  onClick={downloadPack}
                  className="flex items-center gap-2 rounded-md border border-line bg-panel/72 px-3 py-2 text-sm text-ink-subtle transition duration-150 hover:border-brand hover:text-analytic-hover"
                >
                  <Download size={15} />
                  Download Markdown
                </button>
              </div>

              {resultWarning && (
                <div className="rounded-md border border-warning/30 bg-warning/10 p-3 text-sm text-warning-ink">
                  {resultWarning}
                </div>
              )}

              {analysis ? (
                <Panel title="Fit Analysis">
                  <div className="mb-3 text-4xl font-semibold text-analytic">
                    {analysis.fit_score ?? "No score"}
                    <span className="text-base text-muted"> / 100</span>
                  </div>
                  <p className="text-sm text-ink-subtle">{analysis.summary}</p>
                  <List title="Matched" items={analysis.matched_skills} />
                  <List title="Weak Signals" items={analysis.missing_or_weak_signals} />
                </Panel>
              ) : (
                <Panel title="Model Output">
                  <p className="mb-3 text-sm text-ink-subtle">
                    The model did not return the structured score this page expects. Try Generate Pack again, or use
                    the raw output below.
                  </p>
                  <pre className="max-h-[420px] overflow-auto whitespace-pre-wrap rounded-md border border-line-soft bg-canvas/70 p-3 text-xs leading-5 text-muted">
                    {formatRawCareerResult(result)}
                  </pre>
                </Panel>
              )}

              {tailoredCv && (
                <Panel title="Tailored CV">
                  <p className="mb-3 text-sm font-medium text-ink">{tailoredCv.headline}</p>
                  <p className="text-sm text-ink-subtle">{tailoredCv.professional_summary}</p>
                  <List title="Bullets" items={tailoredCv.tailored_bullets} />
                  <List title="Do Not Claim" items={tailoredCv.do_not_claim} />
                </Panel>
              )}

              {coverLetter && (
                <Panel title="Cover Letter">
                  <pre className="whitespace-pre-wrap text-sm leading-6 text-ink-subtle">{coverLetter.cover_letter}</pre>
                </Panel>
              )}
            </div>
          )}
        </section>
      </main>
    </div>
  );
}

function getCareerAnalysis(result: any): CareerAnalysis | null {
  const candidates = [result?.analysis, result?.application_pack?.analysis];
  const analysis = candidates.find(
    (item) =>
      item &&
      typeof item === "object" &&
      ("fit_score" in item || "summary" in item || "matched_skills" in item),
  );
  if (!analysis) return null;
  return {
    ...analysis,
    matched_skills: normalizeStringArray(analysis.matched_skills),
    missing_or_weak_signals: normalizeStringArray(analysis.missing_or_weak_signals),
  };
}

function readLocalCareerPreferences(): Record<string, string> {
  if (typeof window === "undefined") return {};
  try {
    const raw = window.localStorage.getItem("career-search-criteria");
    const parsed = raw ? JSON.parse(raw) : {};
    if (!parsed || typeof parsed !== "object") return {};
    return Object.fromEntries(
      Object.entries(parsed).filter(([key, value]) => {
        if (key === "remote" || key === "match_mode") return Boolean(value);
        return typeof value === "string" && value.trim().length > 0;
      }),
    ) as Record<string, string>;
  } catch {
    return {};
  }
}

function readLocalCareerProfile(): string {
  if (typeof window === "undefined") return "";
  return window.localStorage.getItem("career-profile") || "";
}

function matchModeLabel(mode: string): string {
  const labels: Record<string, string> = {
    both: "profile + criteria",
    profile: "profile only",
    criteria: "criteria only",
  };
  return labels[mode] || "profile + criteria";
}

function getTailoredCv(result: any) {
  const tailoredCv = result?.tailored_cv || result?.application_pack?.tailored_cv;
  if (!tailoredCv || typeof tailoredCv !== "object") return null;
  return {
    ...tailoredCv,
    tailored_bullets: normalizeStringArray(tailoredCv.tailored_bullets),
    do_not_claim: normalizeStringArray(tailoredCv.do_not_claim),
  };
}

function getCoverLetter(result: any) {
  const coverLetter = result?.cover_letter || result?.application_pack?.cover_letter;
  return coverLetter && typeof coverLetter === "object" ? coverLetter : null;
}

function getResultWarning(result: any): string {
  return result?.warning || result?.analysis?.warning || result?.application_pack?.warning || "";
}

function normalizeStringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.map((item) => String(item)).filter(Boolean) : [];
}

function formatRawCareerResult(result: any): string {
  const raw = result?.application_pack || result?.analysis || result;
  return typeof raw === "string" ? raw : JSON.stringify(raw, null, 2);
}

function jobBelongsToTab(job: CareerJob, tab: CareerTab): boolean {
  if (tab === "found") {
    return isSearchJob(job)
      && ["found", "scored", "opened"].includes(job.status)
      && (typeof job.fit_score !== "number" || job.fit_score < MIN_MATCH_SCORE);
  }
  if (tab === "applied") return job.status === "applied";
  if (tab === "skipped") return job.status === "skipped";
  if (tab === "saved") return job.status === "saved";
  return ["scored", "opened"].includes(job.status) && typeof job.fit_score === "number" && job.fit_score >= MIN_MATCH_SCORE;
}

function isSearchJob(job: CareerJob): boolean {
  return ["adzuna", "reed", "remotive", "arbeitnow", "search"].includes(job.source);
}

function mergeJobs(newJobs: CareerJob[], currentJobs: CareerJob[]): CareerJob[] {
  const seen = new Set<string>();
  return [...newJobs, ...currentJobs].filter((job) => {
    const key = job.id || job.url;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function statusLabel(status: string): string {
  const labels: Record<string, string> = {
    found: "Found",
    saved: "Saved",
    scored: "Match",
    opened: "Reviewing",
    applied: "Applied",
    skipped: "Skipped",
  };
  return labels[status] || status;
}

function jobWorkMode(job: CareerJob): WorkModeFilter | "unknown" {
  const text = `${job.title} ${job.location} ${job.description}`.toLowerCase();
  if (/\bhybrid\b/.test(text)) return "hybrid";
  if (/\b(remote|work from home|home[- ]based|distributed)\b/.test(text)) return "remote";
  if (/\b(on[- ]?site|office[- ]based|in[- ]office)\b/.test(text)) return "onsite";
  if (job.location.trim() && !/\b(remote|worldwide|anywhere)\b/.test(job.location.toLowerCase())) return "onsite";
  return "unknown";
}

function workModeLabel(mode: WorkModeFilter | "unknown"): string {
  const labels: Record<WorkModeFilter | "unknown", string> = {
    all: "All",
    remote: "Remote",
    hybrid: "Hybrid",
    onsite: "On-site",
    unknown: "Unspecified",
  };
  return labels[mode];
}

function compareMatchJobs(left: CareerJob, right: CareerJob, sort: MatchSort): number {
  const leftDate = left.created_at || left.updated_at || 0;
  const rightDate = right.created_at || right.updated_at || 0;
  if (sort === "newest") return rightDate - leftDate;
  if (sort === "oldest") return leftDate - rightDate;
  return (right.fit_score || 0) - (left.fit_score || 0) || rightDate - leftDate;
}

function JobMatchDetails({ analysis }: { analysis?: CareerAnalysis | null }) {
  if (!analysis) return null;
  const matched = normalizeStringArray(analysis.matched_skills);
  const missing = normalizeStringArray(analysis.missing_or_weak_signals);
  if (!analysis.summary && !matched.length && !missing.length) return null;

  return (
    <div className="mb-3 border-t border-line-soft/80 pt-3">
      {analysis.summary && (
        <div className="mb-4">
          <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted">Score reasoning</div>
          <p className="text-sm leading-6 text-ink-subtle">{analysis.summary}</p>
        </div>
      )}
      <div className="grid gap-4 md:grid-cols-2">
        <SignalList title="Matched skills" items={matched} tone="positive" />
        <SignalList title="Missing or weak" items={missing} tone="warning" />
      </div>
    </div>
  );
}

function SignalList({
  title,
  items,
  tone,
}: {
  title: string;
  items: string[];
  tone: "positive" | "warning";
}) {
  if (!items.length) return null;
  return (
    <div>
      <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">{title}</div>
      <ul className="space-y-1.5 text-xs leading-5 text-ink-subtle">
        {items.map((item, index) => (
          <li key={`${item}-${index}`} className="flex items-start gap-2">
            <span className={`mt-2 h-1.5 w-1.5 shrink-0 rounded-full ${tone === "positive" ? "bg-success" : "bg-warning"}`} />
            <span>{item}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Panel({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="app-panel soft-fade-in rounded-md p-4">
      <h2 className="mb-3 text-sm font-semibold text-ink">{title}</h2>
      {children}
    </div>
  );
}

function StatusButton({
  icon,
  label,
  loading,
  onClick,
}: {
  icon: ReactNode;
  label: string;
  loading: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      disabled={loading}
      className="inline-flex items-center gap-1.5 rounded-md border border-line bg-canvas/50 px-2.5 py-1.5 text-xs text-ink transition hover:border-brand hover:text-analytic-hover disabled:opacity-50"
    >
      {loading ? <Loader2 size={13} className="animate-spin" /> : icon}
      {label}
    </button>
  );
}

function List({ title, items }: { title: string; items?: string[] }) {
  if (!items?.length) return null;
  return (
    <div className="mt-4">
      <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">{title}</div>
      <ul className="space-y-2 text-sm text-ink-subtle">
        {items.map((item, index) => (
          <li key={index} className="rounded-md border border-line-soft/80 bg-canvas/58 px-3 py-2">
            {item}
          </li>
        ))}
      </ul>
    </div>
  );
}
