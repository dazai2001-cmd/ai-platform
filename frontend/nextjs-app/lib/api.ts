const BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:5000";
const API_TOKEN = process.env.NEXT_PUBLIC_API_TOKEN || "";

function headers(contentType?: string) {
  const h: Record<string, string> = {};
  if (contentType) h["Content-Type"] = contentType;
  if (API_TOKEN) h["X-API-Token"] = API_TOKEN;
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
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: headers("application/json"),
    body: JSON.stringify(body),
  });
  return parseResponse(res);
}

async function get(path: string) {
  const res = await fetch(`${BASE}${path}`, { headers: headers() });
  return parseResponse(res);
}

async function upload(path: string, formData: FormData) {
  const res = await fetch(`${BASE}${path}`, { method: "POST", headers: headers(), body: formData });
  return parseResponse(res);
}

export const api = {
  // Chat (auto-routed)
  chat: (query: string, sessionId?: string, dataset?: string) =>
    post("/api/chat", { query, session_id: sessionId, dataset }),

  // RAG
  ragAsk: (question: string, sessionId?: string) =>
    post("/api/rag/ask", { question, session_id: sessionId }),
  ragUploadPdf: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return upload("/api/rag/upload/pdf", fd);
  },
  ragUploadUrl: (url: string) => post("/api/rag/upload/url", { url }),
  ragUploadText: (text: string, source: string) =>
    post("/api/rag/upload/text", { text, source }),
  ragStats: () => get("/api/rag/stats"),

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
  clearHistory: (sessionId: string) =>
    fetch(`${BASE}/api/memory/${sessionId}`, { method: "DELETE", headers: headers() }).then(parseResponse),

  // Health & analytics
  health: () => get("/api/health"),
  analyticsSummary: (hours = 24) => get(`/api/analytics/summary?since_hours=${hours}`),
  analyticsRecent: (n = 20) => get(`/api/analytics/recent?n=${n}`),
};
