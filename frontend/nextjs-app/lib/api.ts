const CONFIGURED_BASE = process.env.NEXT_PUBLIC_API_URL || "";
const API_TOKEN = process.env.NEXT_PUBLIC_API_TOKEN || "";
const AUTH_STORAGE_KEY = "ai_platform_auth_token";

function baseUrl() {
  if (typeof window === "undefined") return CONFIGURED_BASE;
  const isLocalHost = ["localhost", "127.0.0.1"].includes(window.location.hostname);
  const pointsAtLocalApi = CONFIGURED_BASE.includes("localhost") || CONFIGURED_BASE.includes("127.0.0.1");
  return !isLocalHost && pointsAtLocalApi ? "" : CONFIGURED_BASE;
}

function headers(contentType?: string) {
  const h: Record<string, string> = {};
  if (contentType) h["Content-Type"] = contentType;
  if (API_TOKEN) h["X-API-Token"] = API_TOKEN;
  if (typeof window !== "undefined") {
    const authToken = window.localStorage.getItem(AUTH_STORAGE_KEY);
    if (authToken) h.Authorization = `Bearer ${authToken}`;
  }
  return h;
}

async function parseResponse(res: Response) {
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.error || `Request failed (${res.status})`);
  }
  return data;
}

async function post(path: string, body: object) {
  const res = await fetch(`${baseUrl()}${path}`, {
    method: "POST",
    headers: headers("application/json"),
    body: JSON.stringify(body),
  });
  return parseResponse(res);
}

async function postRaw(path: string, body: object) {
  const res = await fetch(`${baseUrl()}${path}`, {
    method: "POST",
    headers: headers("application/json"),
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `Request failed (${res.status})`);
  }
  return res;
}

async function get(path: string) {
  const res = await fetch(`${baseUrl()}${path}`, { headers: headers() });
  return parseResponse(res);
}

async function upload(path: string, formData: FormData) {
  const res = await fetch(`${baseUrl()}${path}`, { method: "POST", headers: headers(), body: formData });
  return parseResponse(res);
}

export const api = {
  authStorageKey: AUTH_STORAGE_KEY,
  signup: (email: string, password: string) => post("/api/auth/signup", { email, password }),
  login: (email: string, password: string) => post("/api/auth/login", { email, password }),
  verifyEmail: (token: string) => get(`/api/auth/verify?token=${encodeURIComponent(token)}`),
  resendVerification: (email: string) => post("/api/auth/resend-verification", { email }),
  me: () => get("/api/auth/me"),
  logout: () => post("/api/auth/logout", {}),

  // Chat (auto-routed)
  chat: (query: string, sessionId?: string, dataset?: string) =>
    post("/api/chat", { query, session_id: sessionId, dataset }),
  generalChat: (query: string, sessionId?: string, model?: string) =>
    post("/api/chat/general", { query, session_id: sessionId, model }),
  generalChatStream: (query: string, sessionId?: string, model?: string) =>
    postRaw("/api/chat/general/stream", { query, session_id: sessionId, model }),
  chatConversations: () => get("/api/chat/conversations"),
  createChatConversation: (id?: string, title?: string) =>
    post("/api/chat/conversations", { id, title }),
  getChatConversation: (id: string) => get(`/api/chat/conversations/${id}`),
  saveChatConversation: (id: string, title: string, messages: any[]) =>
    fetch(`${baseUrl()}/api/chat/conversations/${id}`, {
      method: "PUT",
      headers: headers("application/json"),
      body: JSON.stringify({ title, messages }),
    }).then(parseResponse),
  deleteChatConversation: (id: string) =>
    fetch(`${baseUrl()}/api/chat/conversations/${id}`, { method: "DELETE", headers: headers() }).then(parseResponse),

  // RAG
  ragAsk: (question: string, sessionId?: string) =>
    post("/api/rag/ask", { question, session_id: sessionId }),
  ragAskStream: (question: string, sessionId?: string) =>
    postRaw("/api/rag/ask/stream", { question, session_id: sessionId }),
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
  ragUploadText: (text: string, source: string) =>
    post("/api/rag/upload/text", { text, source }),
  ragStats: () => get("/api/rag/stats"),
  ragDocuments: () => get("/api/rag/documents"),
  ragDocumentPreview: (source: string) => get(`/api/rag/documents/${encodeURIComponent(source)}`),
  ragDeleteDocument: (source: string) =>
    fetch(`${baseUrl()}/api/rag/documents/${encodeURIComponent(source)}`, { method: "DELETE", headers: headers() }).then(parseResponse),

  // Jobs
  job: (jobId: string) => get(`/api/jobs/${jobId}`),

  // BI
  biAsk: (question: string, sessionId?: string, dataset?: string) =>
    post("/api/bi/ask", { question, session_id: sessionId, dataset }),
  biUpload: (file: File, name: string) => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("name", name);
    return upload("/api/bi/upload", fd);
  },
  biDatasets: () => get("/api/bi/datasets"),
  biSample: (name: string) => get(`/api/bi/datasets/${name}/sample`),

  // Memory
  getHistory: (sessionId: string) => get(`/api/memory/${sessionId}`),
  memorySessions: () => get("/api/memory/sessions"),
  memoryFacts: () => get("/api/memory/facts"),
  addMemoryFact: (content: string) => post("/api/memory/facts", { content }),
  deleteMemoryFact: (factId: string) =>
    fetch(`${baseUrl()}/api/memory/facts/${factId}`, { method: "DELETE", headers: headers() }).then(parseResponse),
  clearHistory: (sessionId: string) =>
    fetch(`${baseUrl()}/api/memory/${sessionId}`, { method: "DELETE", headers: headers() }).then(parseResponse),

  // Health & analytics
  health: () => get("/api/health"),
  warmup: (model?: string) => post("/api/health/warmup", { model }),
  modelSettings: () => get("/api/settings/models"),
  updateModelSettings: (taskModels: Record<string, string>) =>
    fetch(`${baseUrl()}/api/settings/models`, {
      method: "PUT",
      headers: headers("application/json"),
      body: JSON.stringify({ task_models: taskModels }),
    }).then(parseResponse),
  resetModelSettings: () =>
    fetch(`${baseUrl()}/api/settings/models`, { method: "DELETE", headers: headers() }).then(parseResponse),
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
};
