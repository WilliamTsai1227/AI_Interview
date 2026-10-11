// API 包裝：access token 只存在記憶體；401 TOKEN_EXPIRED 自動 refresh 後重送一次；統一錯誤格式。
const BASE = "/api/v1";
let accessToken = null;
let refreshing = null;
let onLoggedOut = () => {};

export class ApiError extends Error {
  constructor(status, body, headers) {
    const e = body?.error || {};
    super(e.message || "發生錯誤，請再試一次");
    this.status = status;
    this.code = e.code || "HTTP_ERROR";
    this.details = e.details || {};
    this.requestId = e.request_id;
    this.retryAfter = Number(headers?.get?.("Retry-After")) || null;
  }
}

export const setToken = (t) => { accessToken = t; };
export const hasToken = () => !!accessToken;
export const setLoggedOutHandler = (fn) => { onLoggedOut = fn; };

async function doRefresh() {
  if (!refreshing) {
    refreshing = fetch(`${BASE}/auth/refresh`, { method: "POST", credentials: "same-origin" })
      .then(async (r) => {
        if (!r.ok) throw new ApiError(r.status, await r.json().catch(() => null), r.headers);
        const b = await r.json();
        accessToken = b.access_token;
        return b;
      })
      .finally(() => { refreshing = null; });
  }
  return refreshing;
}

export async function tryRestoreSession() {
  try { return await doRefresh(); } catch { return null; }
}

export async function api(path, { method = "GET", body, headers = {}, idempotencyKey, retry = true, keepalive = false } = {}) {
  const h = { ...headers };
  if (body !== undefined) h["Content-Type"] = "application/json";
  if (accessToken) h.Authorization = `Bearer ${accessToken}`;
  if (idempotencyKey) h["Idempotency-Key"] = idempotencyKey;
  let r;
  try {
    r = await fetch(BASE + path, { method, headers: h, body: body === undefined ? undefined : JSON.stringify(body),
      credentials: "same-origin", keepalive });
  } catch (e) {
    const err = new ApiError(0, { error: { code: "NETWORK", message: "網路連線不穩，請再試一次" } });
    err.cause = e;
    throw err;
  }
  if (r.status === 401 && retry && !path.startsWith("/auth/")) {
    try {
      await doRefresh();
    } catch {
      accessToken = null;
      onLoggedOut();
      throw new ApiError(401, await r.json().catch(() => null), r.headers);
    }
    return api(path, { method, body, headers, idempotencyKey, retry: false, keepalive });
  }
  if (r.status === 204) return null;
  const data = await r.json().catch(() => null);
  if (!r.ok) throw new ApiError(r.status, data, r.headers);
  return data;
}

export const get = (p) => api(p);
export const post = (p, body, opts = {}) => api(p, { method: "POST", body: body ?? {}, ...opts });
export const patch = (p, body) => api(p, { method: "PATCH", body });
export const put = (p, body) => api(p, { method: "PUT", body });
export const del = (p, body) => api(p, { method: "DELETE", body });

/** 直接上傳到物件儲存：POST /uploads → PUT 預簽名網址 → 回傳 upload_id。 */
export async function uploadFile(purpose, blob, { filename, attemptId } = {}) {
  const type = (blob.type || "application/octet-stream").split(";")[0];
  const t = await post("/uploads", { purpose, content_type: type, size_bytes: blob.size, filename, attempt_id: attemptId });
  let r;
  try {
    r = await fetch(t.url, { method: "PUT", body: blob, headers: t.headers });
  } catch (e) {
    throw new ApiError(0, { error: { code: "NETWORK", message: "上傳失敗，請檢查網路" } });
  }
  if (!r.ok) throw new ApiError(r.status, { error: { code: "UPLOAD_FAILED", message: "上傳失敗，請再試一次" } });
  return t.upload_id;
}

/** SSE（EventSource 不能帶 Authorization，所以用 fetch 串流讀取）。回傳 abort 函式。 */
export function sse(path, onEvent, onEnd) {
  const ctrl = new AbortController();
  (async () => {
    try {
      const r = await fetch(BASE + path, { headers: { Authorization: `Bearer ${accessToken}` }, signal: ctrl.signal });
      if (!r.ok || !r.body) throw new Error("sse failed");
      const reader = r.body.getReader();
      const dec = new TextDecoder();
      let buf = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let i;
        while ((i = buf.indexOf("\n\n")) >= 0) {
          const chunk = buf.slice(0, i);
          buf = buf.slice(i + 2);
          const ev = /^event: (.*)$/m.exec(chunk)?.[1];
          const data = /^data: (.*)$/m.exec(chunk)?.[1];
          if (ev && data) onEvent(ev, JSON.parse(data));
        }
      }
      onEnd?.(null);
    } catch (e) {
      if (!ctrl.signal.aborted) onEnd?.(e);
    }
  })();
  return () => ctrl.abort();
}

export const uuid = () => (crypto.randomUUID ? crypto.randomUUID()
  : "10000000-1000-4000-8000-100000000000".replace(/[018]/g, (c) => (c ^ (crypto.getRandomValues(new Uint8Array(1))[0] & (15 >> (c / 4)))).toString(16)));
