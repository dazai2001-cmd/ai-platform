import { REQUEST_TIMEOUT_MS, STREAM_START_TIMEOUT_MS } from "./request-timeouts";

const CONFIGURED_BASE = process.env.NEXT_PUBLIC_API_URL || "";

type RequestDeadline = {
  signal: AbortSignal;
  race<T>(promise: Promise<T>): Promise<T>;
  releaseConnection(): void;
  cleanup(): void;
};

export type CareerProfileImportResult = {
  cv_text: string;
  updated_at: number | null;
  filename: string;
  file_type: "pdf" | "docx";
  characters: number;
  pages: number | null;
  used_ocr: boolean;
};

export type LocalMediaJob = {
  id: string;
  kind: "image" | "video";
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled" | "interrupted";
  progress: number;
  message: string;
  artifact_url?: string | null;
  error?: string | null;
  worker_active?: boolean;
  spec: { prompt: string; style: string; width: number; height: number; model_id: string; seconds?: number; fps?: number };
};

export type LocalQuestion = {
  kind: string;
  question: string;
  options?: string[];
  fields?: { id: string; label: string; options: string[] }[];
  draft?: Record<string, unknown>;
};

export type LocalCapabilities = {
  enabled: boolean;
  media_loading?: boolean;
  checkpoint_ready?: boolean;
  orchestrator?: string;
  context_tokens?: number;
  models?: { id: string; kind: string; ready: boolean; installed: boolean; reason: string; description: string; license_url: string }[];
};

export type LocalRun = {
  id: string;
  session_id: string;
  query: string;
  status: string;
  message?: string | null;
  model: string;
  model_calls: number;
  tool_calls: number;
  trace: { tool: string; action?: string; status: string; model?: string }[];
  question?: LocalQuestion | null;
  result?: { answer: string; media_job?: LocalMediaJob; [key: string]: any } | null;
  error?: string | null;
};

function timeoutError() {
  const error = new Error("Request timed out");
  error.name = "TimeoutError";
  return error;
}

function abortError(signal: AbortSignal) {
  if (signal.reason instanceof Error) return signal.reason;
  const error = new Error(typeof signal.reason === "string" ? signal.reason : "Request aborted");
  error.name = "AbortError";
  return error;
}

function createDeadline(externalSignal: AbortSignal | undefined, timeoutMs: number): RequestDeadline {
  const controller = new AbortController();
  let active = true;
  let timeout: ReturnType<typeof setTimeout> | undefined;
  let rejectDeadline!: (reason: Error) => void;
  const expired = new Promise<never>((_resolve, reject) => {
    rejectDeadline = reject;
  });

  const abort = (reason: Error) => {
    if (!active) return;
    active = false;
    controller.abort(reason);
    rejectDeadline(reason);
  };
  const onExternalAbort = () => abort(abortError(externalSignal!));

  if (externalSignal?.aborted) {
    onExternalAbort();
  } else {
    externalSignal?.addEventListener("abort", onExternalAbort, { once: true });
    timeout = setTimeout(() => abort(timeoutError()), timeoutMs);
  }

  const clearRequestTimeout = () => {
    if (timeout !== undefined) {
      clearTimeout(timeout);
      timeout = undefined;
    }
  };

  return {
    signal: controller.signal,
    race: <T>(promise: Promise<T>) => Promise.race([promise, expired]),
    releaseConnection: () => {
      // A successful streaming response has connected. Stop the connection
      // deadline, but keep forwarding the caller's signal to its response body.
      clearRequestTimeout();
      if (!externalSignal) active = false;
    },
    cleanup: () => {
      active = false;
      clearRequestTimeout();
      externalSignal?.removeEventListener("abort", onExternalAbort);
    },
  };
}

function baseUrl() {
  if (typeof window === "undefined") return CONFIGURED_BASE;
  const isLocalHost = ["localhost", "127.0.0.1"].includes(window.location.hostname);
  const pointsAtLocalApi = CONFIGURED_BASE.includes("localhost") || CONFIGURED_BASE.includes("127.0.0.1");
  return !isLocalHost && pointsAtLocalApi ? "" : CONFIGURED_BASE;
}

function headers(contentType?: string) {
  const h: Record<string, string> = {};
  if (contentType) h["Content-Type"] = contentType;
  return h;
}

async function parseResponse(res: Response, deadline: RequestDeadline, allowDegradedHealth = false) {
  let data: any = {};
  try {
    data = await deadline.race(res.json());
  } catch (error) {
    // Preserve the existing non-JSON fallback, but never swallow a request
    // timeout or caller-initiated abort while the body is being parsed.
    if (deadline.signal.aborted) throw error;
  }
  const degradedHealth = allowDegradedHealth && res.status === 503
    && data?.status === "degraded" && data.checks && typeof data.checks === "object";
  if (!res.ok && !degradedHealth) {
    throw new Error(data?.error || `Request failed (${res.status})`);
  }
  return data;
}

async function request(path: string, init: RequestInit = {}, allowDegradedHealth = false) {
  const deadline = createDeadline(init.signal || undefined, REQUEST_TIMEOUT_MS);
  try {
    const res = await deadline.race(fetch(`${baseUrl()}${path}`, {
      ...init,
      credentials: "include",
      signal: deadline.signal,
    }));
    return await parseResponse(res, deadline, allowDegradedHealth);
  } finally {
    deadline.cleanup();
  }
}

async function post(path: string, body: object, signal?: AbortSignal) {
  return request(path, {
    method: "POST",
    headers: headers("application/json"),
    body: JSON.stringify(body),
    signal,
  });
}

async function postRaw(path: string, body: object, externalSignal?: AbortSignal) {
  const deadline = createDeadline(externalSignal, STREAM_START_TIMEOUT_MS);
  let connected = false;
  try {
    const res = await deadline.race(fetch(`${baseUrl()}${path}`, {
      method: "POST",
      headers: headers("application/json"),
      body: JSON.stringify(body),
      credentials: "include",
      signal: deadline.signal,
    }));
    if (!res.ok) {
      await parseResponse(res, deadline);
    }
    if (deadline.signal.aborted) throw abortError(deadline.signal);
    deadline.releaseConnection();
    connected = true;
    return res;
  } finally {
    if (!connected) deadline.cleanup();
  }
}

async function get(path: string, allowDegradedHealth = false) {
  return request(path, { headers: headers() }, allowDegradedHealth);
}

async function upload(path: string, formData: FormData) {
  return request(path, { method: "POST", headers: headers(), body: formData });
}

async function waitForLocalRun(runId: string, signal?: AbortSignal) {
  const started = Date.now();
  try {
    while (Date.now() - started < 300_000) {
      if (signal?.aborted) throw abortError(signal);
      const run: LocalRun = await request(`/api/local/runs/${encodeURIComponent(runId)}`, { signal });
      if (run.status === "awaiting_input") {
        return { answer: run.question?.question || "Please clarify your request.", needs_clarification: true,
                 clarification: run.question, run_id: run.id, route: "local", model: run.model };
      }
      if (["complete", "limited"].includes(run.status)) {
        return { ...run.result, answer: run.result?.answer || "Task complete.", run_id: run.id,
                 model: run.result?.model || run.model, route: run.result?.route || "local" };
      }
      if (["failed", "cancelled", "interrupted"].includes(run.status)) {
        throw new Error(run.error || `Local task ${run.status}.`);
      }
      await new Promise<void>((resolve, reject) => {
        const onAbort = () => { clearTimeout(timer); signal?.removeEventListener("abort", onAbort); reject(abortError(signal!)); };
        const timer = setTimeout(() => { signal?.removeEventListener("abort", onAbort); resolve(); }, 1000);
        signal?.addEventListener("abort", onAbort, { once: true });
        if (signal?.aborted) onAbort();
      });
    }
    return { answer: "The local task is still running. Open Activity in Workspace to follow its progress.", route: "local", run_id: runId };
  } catch (error) {
    // Leaving the view detaches polling; durable work is cancelled only by Stop.
    if (signal?.aborted && signal.reason !== "unmount") {
      void post(`/api/local/runs/${encodeURIComponent(runId)}/cancel`, {}).catch(() => {});
    }
    throw error;
  }
}

export const api = {
  signup: (email: string, password: string) => post("/api/auth/signup", { email, password }),
  login: (email: string, password: string) => post("/api/auth/login", { email, password }),
  verifyEmail: (token: string) => get(`/api/auth/verify?token=${encodeURIComponent(token)}`),
  resendVerification: (email: string) => post("/api/auth/resend-verification", { email }),
  me: () => get("/api/auth/me"),
  logout: () => post("/api/auth/logout", {}),

  // Chat (auto-routed)
  chat: (query: string, sessionId?: string, dataset?: string, signal?: AbortSignal) =>
    post("/api/chat", { query, session_id: sessionId, dataset }, signal),
  chatStream: (query: string, sessionId?: string, dataset?: string, signal?: AbortSignal) =>
    postRaw("/api/chat/stream", { query, session_id: sessionId, dataset }, signal),
  workspaceChat: async (query: string, sessionId?: string, signal?: AbortSignal) => {
    const result = await post("/api/chat/workspace", { query, session_id: sessionId }, signal);
    return result.local_run ? waitForLocalRun(result.local_run, signal) : result;
  },
  generalChat: (query: string, sessionId?: string, model?: string, signal?: AbortSignal) =>
    post("/api/chat/general", { query, session_id: sessionId, model }, signal),
  generalChatStream: (query: string, sessionId?: string, model?: string, signal?: AbortSignal) =>
    postRaw("/api/chat/general/stream", { query, session_id: sessionId, model }, signal),
  chatConversations: () => get("/api/chat/conversations"),
  createChatConversation: (id?: string, title?: string) =>
    post("/api/chat/conversations", { id, title }),
  getChatConversation: (id: string) => get(`/api/chat/conversations/${id}`),
  saveChatConversation: (id: string, title: string, messages: any[]) =>
    request(`/api/chat/conversations/${id}`, {
      method: "PUT",
      headers: headers("application/json"),
      body: JSON.stringify({ title, messages }),
    }),
  deleteChatConversation: (id: string) =>
    request(`/api/chat/conversations/${id}`, { method: "DELETE", headers: headers() }),

  // These endpoints are disabled by the backend in cloud/production runtimes.
  localCapabilities: (includeMedia = true) => get(`/api/local/capabilities${includeMedia ? "" : "?media=false"}`) as Promise<LocalCapabilities>,
  localRuns: () => get("/api/local/runs") as Promise<LocalRun[]>,
  waitLocalTask: (id: string, signal?: AbortSignal) => waitForLocalRun(id, signal),
  startLocalRun: (query: string, sessionId?: string) => post("/api/local/runs", { query, session_id: sessionId }) as Promise<LocalRun>,
  resumeLocalRun: (id: string, answer: string) => post(`/api/local/runs/${encodeURIComponent(id)}/resume`, { answer }) as Promise<LocalRun>,
  resumeLocalTask: async (id: string, answer: string, signal?: AbortSignal) => {
    await post(`/api/local/runs/${encodeURIComponent(id)}/resume`, { answer }, signal);
    return waitForLocalRun(id, signal);
  },
  cancelLocalRun: (id: string) => post(`/api/local/runs/${encodeURIComponent(id)}/cancel`, {}),
  localMedia: () => get("/api/local/media") as Promise<LocalMediaJob[]>,
  localMediaJob: (id: string) => get(`/api/local/media/${encodeURIComponent(id)}`) as Promise<LocalMediaJob>,
  cancelLocalMedia: (id: string) => post(`/api/local/media/${encodeURIComponent(id)}/cancel`, {}),
  deleteLocalMedia: (id: string) => request(`/api/local/media/${encodeURIComponent(id)}`, { method: "DELETE" }),
  localArtifactUrl: (path: string) => `${baseUrl()}${path}`,

  // RAG
  ragAsk: (question: string, sessionId?: string, signal?: AbortSignal) =>
    post("/api/rag/ask", { question, session_id: sessionId }, signal),
  ragAskStream: (question: string, sessionId?: string, signal?: AbortSignal) =>
    postRaw("/api/rag/ask/stream", { question, session_id: sessionId }, signal),
  ragUploadPdf: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return upload("/api/rag/upload/pdf", fd);
  },
  ragUploadPdfAsync: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return upload("/api/rag/upload/pdf/async", fd);
  },
  ragUploadUrl: (url: string) => post("/api/rag/upload/url", { url }),
  ragUploadUrlAsync: (url: string) => post("/api/rag/upload/url/async", { url }),
  ragUploadText: (text: string, source: string) =>
    post("/api/rag/upload/text", { text, source }),
  ragStats: () => get("/api/rag/stats"),
  ragDocuments: () => get("/api/rag/documents"),
  ragDocumentPreview: (source: string) => get(`/api/rag/documents/${encodeURIComponent(source)}`),
  ragDeleteDocument: (source: string) =>
    request(`/api/rag/documents/${encodeURIComponent(source)}`, { method: "DELETE", headers: headers() }),

  // Jobs
  job: (jobId: string) => get(`/api/jobs/${jobId}`),

  // BI
  biAsk: (question: string, sessionId?: string, dataset?: string, signal?: AbortSignal) =>
    post("/api/bi/ask", { question, session_id: sessionId, dataset }, signal),
  biUpload: (file: File, name: string) => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("name", name);
    return upload("/api/bi/upload", fd);
  },
  biDatasets: () => get("/api/bi/datasets"),
  biSample: (name: string) => get(`/api/bi/datasets/${encodeURIComponent(name)}/sample`),
  biDeleteDataset: (name: string) =>
    request(`/api/bi/datasets/${encodeURIComponent(name)}`, { method: "DELETE", headers: headers() }),

  // Memory
  getHistory: (sessionId: string) => get(`/api/memory/${sessionId}`),
  memorySessions: () => get("/api/memory/sessions"),
  memoryFacts: () => get("/api/memory/facts"),
  addMemoryFact: (content: string) => post("/api/memory/facts", { content }),
  deleteMemoryFact: (factId: string) =>
    request(`/api/memory/facts/${factId}`, { method: "DELETE", headers: headers() }),
  clearHistory: (sessionId: string) =>
    request(`/api/memory/${sessionId}`, { method: "DELETE", headers: headers() }),

  // Health & analytics
  health: () => get("/api/health", true),
  warmup: (model?: string) => post("/api/health/warmup", { model }),
  modelSettings: () => get("/api/settings/models"),
  updateModelSettings: (taskModels: Record<string, string>) =>
    request("/api/settings/models", {
      method: "PUT",
      headers: headers("application/json"),
      body: JSON.stringify({ task_models: taskModels }),
    }),
  resetModelSettings: () =>
    request("/api/settings/models", { method: "DELETE", headers: headers() }),
  analyticsSummary: (hours = 24) => get(`/api/analytics/summary?since_hours=${hours}`),
  analyticsRecent: (n = 20) => get(`/api/analytics/recent?n=${n}`),

  // Career
  careerAnalyze: (cvText: string, jobDescription: string, model?: string) =>
    post("/api/career/analyze", { cv_text: cvText, job_description: jobDescription, model }),
  careerTailor: (cvText: string, jobDescription: string, model?: string) =>
    post("/api/career/tailor", { cv_text: cvText, job_description: jobDescription, model }),
  careerCoverLetter: (cvText: string, jobDescription: string, model?: string) =>
    post("/api/career/cover-letter", { cv_text: cvText, job_description: jobDescription, model }),
  careerPack: (cvText: string, jobDescription: string, model?: string) =>
    post("/api/career/pack", { cv_text: cvText, job_description: jobDescription, model }),
  careerProfile: () => get("/api/career/profile"),
  careerImportProfile: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return upload("/api/career/profile/import", fd) as Promise<CareerProfileImportResult>;
  },
  updateCareerProfile: (cvText: string) =>
    request("/api/career/profile", {
      method: "PUT",
      headers: headers("application/json"),
      body: JSON.stringify({ cv_text: cvText }),
    }),
  deleteCareerProfile: () =>
    request("/api/career/profile", { method: "DELETE", headers: headers() }),
  careerPreferences: () => get("/api/career/preferences"),
  updateCareerPreferences: (preferences: Record<string, string>) =>
    request("/api/career/preferences", {
      method: "PUT",
      headers: headers("application/json"),
      body: JSON.stringify(preferences),
    }),
  careerJobs: () => get("/api/career/jobs"),
  saveCareerJob: (job: {
    description: string;
    title?: string;
    company?: string;
    location?: string;
    url?: string;
    source?: string;
    cv_text?: string;
  }) => post("/api/career/jobs", job),
  importCareerJobUrl: (url: string, cvText?: string) =>
    post("/api/career/jobs/import-url", { url, cv_text: cvText }),
  searchCareerJobs: (cvText?: string, limit = 10) =>
    post("/api/career/jobs/search", { cv_text: cvText, limit }),
  searchCareerJobsStream: (cvText?: string, limit = 10, signal?: AbortSignal) =>
    postRaw("/api/career/jobs/search/stream", { cv_text: cvText, limit }, signal),
  scoreCareerJob: (jobId: string, cvText: string) =>
    post(`/api/career/jobs/${jobId}/score`, { cv_text: cvText }),
  generateCareerMatchPack: (jobId: string, cvText: string) =>
    post(`/api/career/jobs/${jobId}/pack`, { cv_text: cvText }),
  startCareerScoreBatch: (cvText: string, jobIds?: string[]) =>
    post("/api/career/jobs/score-batches", { cv_text: cvText, job_ids: jobIds }),
  currentCareerScoreBatch: () => get("/api/career/jobs/score-batches/current"),
  careerScoreBatch: (batchId: string) => get(`/api/career/jobs/score-batches/${batchId}`),
  cancelCareerScoreBatch: (batchId: string) => post(`/api/career/jobs/score-batches/${batchId}/cancel`, {}),
  updateCareerJobStatus: (jobId: string, status: string) =>
    request(`/api/career/jobs/${jobId}/status`, {
      method: "PUT",
      headers: headers("application/json"),
      body: JSON.stringify({ status }),
    }),
  deleteCareerJob: (jobId: string) =>
    request(`/api/career/jobs/${jobId}`, { method: "DELETE", headers: headers() }),
};
