// 全域狀態：目前使用者、目標職缺快取、介面偏好。伺服器才是真實來源，這裡只是快取。
import { get } from "./api.js";

export const MODES = { quick: { n: 3, name: "快速", sub: "3 題・約 8 分鐘" }, standard: { n: 5, name: "標準", sub: "5 題・約 15 分鐘" }, deep: { n: 8, name: "深入", sub: "8 題・約 25 分鐘" } };
export const PERSONAS = { warm: { name: "溫和引導", sub: "適合第一次練習" }, real: { name: "真實模擬", sub: "像正式面試一樣" }, tough: { name: "壓力面試", sub: "會追問細節" } };
export const LANGS = { zh: "中文", en: "English", mixed: "中英混合" };
export const CATS = ["自我介紹", "技術深度", "系統設計", "團隊合作", "情境題", "動機與規劃"];
export const DIFF = { basic: "基礎", medium: "中等", advanced: "進階" };
export const EST = { basic: 2, medium: 3, advanced: 4 };

export const S = {
  me: null,            // GET /me
  targets: null,       // GET /targets items
  resumes: null,       // GET /resumes items
  job: null,           // 新面試選的目標職缺 id
  resumeSel: undefined,
  theme: null,
  route: null,
};
try { S.theme = localStorage.getItem("ai-theme"); } catch { /* 無痕模式 */ }
try { S.job = localStorage.getItem("ai-last-target"); } catch { /* ignore */ }

export function pickTarget(id) {
  S.job = id;
  try { localStorage.setItem("ai-last-target", id); } catch { /* ignore */ }
}

export async function loadMe() { S.me = await get("/me"); return S.me; }
export async function loadTargets() { S.targets = (await get("/targets")).items; return S.targets; }
export async function loadResumes() { S.resumes = (await get("/resumes")).items; return S.resumes; }

export const prefs = () => S.me?.preferences || { default_length: "standard", default_persona: "warm", default_language: "zh" };
export const shortName = () => {
  const n = (S.me?.user?.display_name || "").trim();
  return n ? n.slice(-1) : "?";
};

export function fmtDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  const p = (x) => String(x).padStart(2, "0");
  return `${p(d.getMonth() + 1)}/${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}
export function ago(iso) {
  if (!iso) return "";
  const days = Math.floor((Date.now() - new Date(iso)) / 86400000);
  return days <= 0 ? "今天" : days < 7 ? `${days} 天前` : `${Math.floor(days / 7)} 週前`;
}
export const fmtClock = (s) => `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
export const fmtSize = (b) => (b > 1048576 ? `${(b / 1048576).toFixed(1)} MB` : `${Math.round(b / 1024)} KB`);

export function fuLabel(f) {
  if (!f) return "Ava 判斷（最多 1 次）";
  return f.mode === "none" ? "不追問" : f.mode === "fixed" ? `固定追問 ${f.count} 次`
    : f.mode === "planned" ? `預設追問 ${f.planned.length} 題` : `Ava 判斷（最多 ${f.count} 次）`;
}
export function fuExpected(f) {
  return !f ? 0.5 : f.mode === "fixed" ? f.count : f.mode === "planned" ? f.planned.length : f.mode === "adaptive" ? f.count / 2 : 0;
}
