// API 包裝：access token 只存在記憶體；401 TOKEN_EXPIRED 自動 refresh 後重送一次；統一錯誤格式。
// 部署換版、網路瞬斷時自動重送，但只重送「再送一次也不會重複執行」的請求：GET，或帶 Idempotency-Key 的。
const BASE = "/api/v1";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// 502／504 一定是代理層（Traefik）連不到後端；503 只有代理層回的才重送，API 自己回的 503（JSON，例如同時面試的人太多）照常顯示
const proxyError = (r) => r.status === 502 || r.status === 504
  || (r.status === 503 && !(r.headers.get("Content-Type") || "").includes("application/json"));

async function fetchRetry(url, opts, safe, tries = 3) {
  for (let i = 0; ; i++) {
    let r;
    try {
      r = await fetch(url, opts);
    } catch (e) {
      if (!safe || i >= tries - 1) throw e;
      await sleep(400 * 2 ** i);
      continue;
    }
    if (!safe || i >= tries - 1 || !proxyError(r)) return r;
    await sleep(400 * 2 ** i);
  }
}
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
    r = await fetchRetry(BASE + path, { method, headers: h, body: body === undefined ? undefined : JSON.stringify(body),
      credentials: "same-origin", keepalive }, method === "GET" || !!idempotencyKey);
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

/** 下載檔案（帶登入憑證）→ Blob；401 時自動 refresh 後重送一次。 */
export async function getBlob(path, retry = true) {
  let r;
  try { r = await fetchRetry(BASE + path, { headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {}, credentials: "same-origin" }, true); }
  catch { throw new ApiError(0, { error: { code: "NETWORK", message: "網路連線不穩，請再試一次" } }); }
  if (r.status === 401 && retry) {
    try { await doRefresh(); } catch { accessToken = null; onLoggedOut(); throw new ApiError(401, null, r.headers); }
    return getBlob(path, false);
  }
  if (!r.ok) throw new ApiError(r.status, await r.json().catch(() => null), r.headers);
  return r.blob();
}

export const get = (p) => api(p);
export const post = (p, body, opts = {}) => api(p, { method: "POST", body: body ?? {}, ...opts });
export const patch = (p, body) => api(p, { method: "PATCH", body });
export const put = (p, body) => api(p, { method: "PUT", body });
export const del = (p, body) => api(p, { method: "DELETE", body });

/** 直接上傳到物件儲存：POST /uploads → PUT 預簽名網址 → 回傳 upload_id。
 *  檔案不經過 API，部署換版不會打斷上傳。PUT 同一個網址重送是覆蓋同一個物件，可以放心重試；
 *  網址過期（403）時重新拿一張上傳單。 */
export async function uploadFile(purpose, blob, { filename, attemptId } = {}) {
  const type = (blob.type || "application/octet-stream").split(";")[0];
  const ticket = () => post("/uploads", { purpose, content_type: type, size_bytes: blob.size, filename, attempt_id: attemptId });
  let t = await ticket();
  for (let i = 0; ; i++) {
    let r = null;
    try { r = await fetch(t.url, { method: "PUT", body: blob, headers: t.headers }); } catch { /* 網路中斷：重試 */ }
    if (r?.ok) return t.upload_id;
    if (i >= 3) {
      if (!r) throw new ApiError(0, { error: { code: "NETWORK", message: "上傳失敗，請檢查網路" } });
      throw new ApiError(r.status, { error: { code: "UPLOAD_FAILED", message: "上傳失敗，請再試一次" } });
    }
    if (r && r.status < 500 && r.status !== 403) {
      throw new ApiError(r.status, { error: { code: "UPLOAD_FAILED", message: "上傳失敗，請再試一次" } });
    }
    if (r?.status === 403) t = await ticket();
    await sleep(500 * 2 ** i);
  }
}

/** SSE（EventSource 不能帶 Authorization，所以用 fetch 串流讀取）。回傳 abort 函式。
 *  連線會因為伺服器時限（120 秒）或部署換版而結束，呼叫端在 onEnd 決定要不要重連。 */
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
