const API_INTERNAL_URL = process.env.API_INTERNAL_URL || "http://api:5000";

type Params = {
  params: {
    path: string[];
  };
};

function targetUrl(path: string[], request: Request) {
  const url = new URL(request.url);
  const target = new URL(`/api/${path.join("/")}`, API_INTERNAL_URL);
  target.search = url.search;
  return target.toString();
}

function forwardedHeaders(request: Request) {
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
  return headers;
}

async function proxy(request: Request, { params }: Params) {
  const method = request.method.toUpperCase();
  const hasBody = !["GET", "HEAD"].includes(method);
  const init: RequestInit & { duplex?: "half" } = {
    method,
    headers: forwardedHeaders(request),
    cache: "no-store",
  };

  if (hasBody) {
    init.body = request.body;
    init.duplex = "half";
  }

  const upstream = await fetch(targetUrl(params.path, request), init);
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
