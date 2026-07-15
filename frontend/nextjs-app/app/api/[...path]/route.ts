import { isIP } from "node:net";

const API_INTERNAL_URL = process.env.API_INTERNAL_URL || "http://api:5000";
const API_AUTH_TOKEN = process.env.API_AUTH_TOKEN?.trim() || "";

type Params = {
  params: Promise<{
    path: string[];
  }>;
};

function targetUrl(path: string[], request: Request) {
  const url = new URL(request.url);
  const target = new URL(`/api/${path.join("/")}`, API_INTERNAL_URL);
  target.search = url.search;
  return target.toString();
}

function forwardedHeaders(request: Request) {
  const forwardedChain = (request.headers.get("x-forwarded-for") || "")
    .split(",")
    .map((value) => value.trim())
    .filter(Boolean);
  // The documented edge proxy appends/overwrites the address it observed.
  // Forward only that final, syntactically valid address so earlier
  // browser-controlled XFF values cannot become a rate-limit identity.
  const observedClient = forwardedChain.at(-1) || "";
  const headers = new Headers(request.headers);
  headers.delete("host");
  headers.delete("content-length");
  headers.delete("connection");
  headers.delete("expect");
  headers.delete("keep-alive");
  headers.delete("proxy-authenticate");
  headers.delete("proxy-authorization");
  headers.delete("te");
  headers.delete("trailer");
  headers.delete("transfer-encoding");
  headers.delete("upgrade");
  for (const name of Array.from(headers.keys())) {
    if (name === "forwarded" || name === "x-real-ip" || name.startsWith("x-forwarded-")) {
      headers.delete(name);
    }
  }
  if (isIP(observedClient)) headers.set("x-forwarded-for", observedClient);
  // Never trust a browser-supplied infrastructure credential. The optional
  // API boundary token exists only in the Next server environment.
  headers.delete("x-api-token");
  if (API_AUTH_TOKEN) headers.set("x-api-token", API_AUTH_TOKEN);
  return headers;
}

async function proxy(request: Request, { params }: Params) {
  const { path } = await params;
  const method = request.method.toUpperCase();
  const hasBody = !["GET", "HEAD"].includes(method);
  const init: RequestInit & { duplex?: "half" } = {
    method,
    headers: forwardedHeaders(request),
    cache: "no-store",
    signal: request.signal,
  };

  if (hasBody) {
    init.body = request.body;
    init.duplex = "half";
  }

  const upstream = await fetch(targetUrl(path, request), init);
  const headers = new Headers(upstream.headers);
  headers.delete("content-encoding");
  headers.delete("content-length");

  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers,
  });
}

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
