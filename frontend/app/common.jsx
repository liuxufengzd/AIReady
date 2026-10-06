"use strict";
/* Shared helpers. Declared at the top level so every page script can use them.
   Page scripts wrap themselves in an IIFE so their own names do not collide. */

const { useState, useEffect, useMemo, useRef, useCallback } = React;

function configured(value, fallback) {
  if (value && value.indexOf("__") === -1) return value.replace(/\/+$/, "");
  return fallback;
}

const API_BASE = configured(
  window.APP_CONFIG && window.APP_CONFIG.apiBase,
  window.location.protocol !== "file:" ? window.location.origin : "http://localhost:8003"
);
/** Rows per page in every list. The API allows at most 100. */
const PAGE_SIZE = 100;

const SUPERVISOR_BASE = configured(
  window.APP_CONFIG && window.APP_CONFIG.supervisorBase,
  "http://localhost:8002"
);

// ── HTTP ────────────────────────────────────────────────────────────────────

async function errorDetail(resp) {
  try {
    const j = await resp.json();
    return j.detail
      ? (Array.isArray(j.detail) ? JSON.stringify(j.detail, null, 2) : String(j.detail))
      : JSON.stringify(j);
  } catch {
    try { return await resp.text(); } catch { return resp.statusText; }
  }
}

/** Call the data management API. Returns parsed JSON, or null for an empty body. */
async function api(method, path, queryParams, body, base = API_BASE) {
  const url = new URL(base + path);
  if (queryParams) {
    for (const [k, v] of Object.entries(queryParams)) {
      if (v === null || v === undefined || v === "") continue;
      url.searchParams.set(k, String(v));
    }
  }
  const opts = { method };
  if (body !== undefined) {
    opts.headers = { "Content-Type": "application/json" };
    opts.body = JSON.stringify(body);
  }
  let resp;
  try {
    resp = await fetch(url.toString(), opts);
  } catch (e) {
    throw new Error(`Network error — is the API server running at ${base}?\n${e.message}`);
  }
  if (!resp.ok) throw new Error(`[${resp.status}] ${await errorDetail(resp)}`);
  const text = await resp.text();
  return text.trim() ? JSON.parse(text) : null;
}

function encodePath(path) {
  return String(path || "").split("/").map((part) => encodeURIComponent(part)).join("/");
}

// ── Formatting ──────────────────────────────────────────────────────────────

function formatWhen(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function formatSize(bytes) {
  if (bytes === null || bytes === undefined) return "—";
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit += 1; }
  return `${value.toFixed(value < 10 ? 1 : 0)} ${units[unit]}`;
}

function formatCount(n) {
  return n === null || n === undefined ? "—" : Number(n).toLocaleString();
}

function plural(n, word) {
  return `${n.toLocaleString()} ${word}${n === 1 ? "" : "s"}`;
}

// ── Pipeline state ──────────────────────────────────────────────────────────

const FILE_STATES = {
  extracting: { label: "Extracting", cls: "bg-violet-50 text-violet-700 border-violet-200", dot: "bg-violet-500", live: true },
  failed:     { label: "Failed",     cls: "bg-red-50 text-red-700 border-red-200",          dot: "bg-red-500" },
  reviewing:  { label: "Reviewing",  cls: "bg-amber-50 text-amber-800 border-amber-200",    dot: "bg-amber-500" },
  indexing:   { label: "Indexing",   cls: "bg-sky-50 text-sky-700 border-sky-200",          dot: "bg-sky-500", live: true },
  ready:      { label: "Ready",      cls: "bg-emerald-50 text-emerald-700 border-emerald-200", dot: "bg-emerald-500" },
};

function StateBadge({ state, title }) {
  if (!state) return <span className="text-xs text-slate-300">—</span>;
  const s = FILE_STATES[state] || { label: state, cls: "bg-slate-50 text-slate-600 border-slate-200", dot: "bg-slate-400" };
  return (
    <span
      title={title || undefined}
      className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-bold border whitespace-nowrap ${s.cls}`}
    >
      <span className={`w-1.5 h-1.5 rounded-full ${s.dot} ${s.live ? "pulse-dot" : ""}`} />
      {s.label}
    </span>
  );
}

// ── Primitives ──────────────────────────────────────────────────────────────

function Spinner({ size = 40, color = "#2563eb", track = "#e2e8f0" }) {
  return (
    <div
      className="spinner"
      style={{
        width: size,
        height: size,
        borderRadius: "50%",
        border: `${Math.max(2, size / 10)}px solid ${track}`,
        borderTopColor: color,
        flexShrink: 0,
      }}
    />
  );
}

function LoadingOverlay({ msg }) {
  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(15,23,42,0.45)",
        backdropFilter: "blur(6px)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 100,
      }}
    >
      <div className="bg-white rounded-2xl shadow-2xl px-10 py-8 flex flex-col items-center gap-5 max-w-sm mx-4">
        <Spinner size={48} />
        <div className="text-center">
          <p className="text-slate-700 font-semibold text-base">{msg}</p>
          <p className="text-slate-400 text-xs mt-1">Please wait…</p>
        </div>
      </div>
    </div>
  );
}

function Alert({ type = "error", msg, onClose }) {
  if (!msg) return null;
  const styles = {
    error: "bg-red-50 border-red-200 text-red-800",
    warn: "bg-amber-50 border-amber-200 text-amber-800",
    info: "bg-blue-50 border-blue-200 text-blue-800",
    success: "bg-green-50 border-green-200 text-green-800",
  };
  const icons = { error: "⚠", warn: "⚡", info: "ℹ", success: "✓" };
  return (
    <div className={`mb-4 p-3.5 rounded-xl border flex items-start gap-2.5 ${styles[type]}`}>
      <span className="flex-shrink-0 font-bold mt-0.5">{icons[type]}</span>
      <pre className="flex-1 text-xs whitespace-pre-wrap break-words font-sans">{msg}</pre>
      {onClose && (
        <button onClick={onClose} className="flex-shrink-0 opacity-50 hover:opacity-100 transition-opacity ml-1">
          ✕
        </button>
      )}
    </div>
  );
}

function Card({ children, className = "" }) {
  return (
    <div className={`bg-white rounded-2xl shadow-sm border border-slate-200 overflow-hidden ${className}`}>
      {children}
    </div>
  );
}

function CardHeader({ icon, title, subtitle, tone = "from-blue-50 to-indigo-50", children }) {
  return (
    <div className={`px-6 sm:px-8 py-6 bg-gradient-to-br ${tone} border-b border-slate-100`}>
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div className="flex items-center gap-3 min-w-0">
          {icon && (
            <div className="w-9 h-9 bg-white border border-slate-200 rounded-lg grid place-items-center text-base flex-shrink-0">
              {icon}
            </div>
          )}
          <div className="min-w-0">
            <h2 className="text-xl font-bold text-slate-800 truncate">{title}</h2>
            {subtitle && <div className="text-slate-500 text-sm mt-0.5">{subtitle}</div>}
          </div>
        </div>
        {children && <div className="flex items-center gap-2 flex-shrink-0">{children}</div>}
      </div>
    </div>
  );
}

function Button({ children, onClick, disabled, variant = "secondary", className = "", title, type = "button" }) {
  const variants = {
    primary: "bg-blue-600 hover:bg-blue-700 text-white border-blue-600 shadow-sm shadow-blue-200",
    danger: "bg-red-600 hover:bg-red-700 text-white border-red-600 shadow-sm shadow-red-200",
    secondary: "bg-white hover:bg-slate-50 text-slate-600 border-slate-200",
    ghost: "bg-transparent hover:bg-slate-100 text-slate-600 border-transparent",
  };
  return (
    <button
      type={type}
      title={title}
      onClick={onClick}
      disabled={disabled}
      className={`px-3.5 py-2 text-xs font-semibold rounded-lg border transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${variants[variant]} ${className}`}
    >
      {children}
    </button>
  );
}

function EmptyState({ icon = "📭", title, children }) {
  return (
    <div className="py-16 text-center">
      <div className="text-3xl mb-3">{icon}</div>
      <p className="text-sm font-semibold text-slate-600">{title}</p>
      {children && <div className="text-xs text-slate-400 mt-1">{children}</div>}
    </div>
  );
}

function SearchInput({ value, onChange, placeholder }) {
  return (
    <div className="relative flex-1 min-w-[12rem]">
      <span className="absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-400 text-sm">⌕</span>
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full pl-9 pr-8 py-2.5 border border-slate-300 rounded-xl text-sm focus:ring-2 focus:ring-blue-500 focus:border-transparent"
      />
      {value && (
        <button
          onClick={() => onChange("")}
          className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-700 text-xs"
          title="Clear"
        >
          ✕
        </button>
      )}
    </div>
  );
}

function NeedProject({ what }) {
  return (
    <Card>
      <EmptyState icon="🗂" title="Choose a project">
        Pick a project in the header to see its {what}.
      </EmptyState>
    </Card>
  );
}

function ConfirmDialog({ title, children, confirmLabel, onConfirm, onCancel, busy, danger = true }) {
  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape" && !busy) onCancel(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onCancel]);
  return (
    <div
      className="fixed inset-0 z-[90] flex items-center justify-center px-4"
      style={{ background: "rgba(15,23,42,0.45)", backdropFilter: "blur(4px)" }}
      onClick={() => { if (!busy) onCancel(); }}
    >
      <div
        role="dialog"
        aria-modal="true"
        className="bg-white rounded-2xl shadow-2xl w-full max-w-md p-6 fade-up"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-lg font-bold text-slate-800">{title}</h3>
        <div className="text-sm text-slate-600 mt-2 space-y-2">{children}</div>
        <div className="flex justify-end gap-2 mt-6">
          <Button onClick={onCancel} disabled={busy}>Cancel</Button>
          <Button variant={danger ? "danger" : "primary"} onClick={onConfirm} disabled={busy}>
            <span className="inline-flex items-center gap-2">
              {busy && <Spinner size={12} color="#fff" track="rgba(255,255,255,0.3)" />}
              {confirmLabel}
            </span>
          </Button>
        </div>
      </div>
    </div>
  );
}

/** Rendered markdown. ``resolveAsset`` turns a relative image path into a URL. */
function MdPreview({ text, resolveAsset, className = "", empty = "No content available." }) {
  const html = useMemo(() => {
    if (!text || !text.trim()) return `<p style="color:#94a3b8;font-style:italic">${empty}</p>`;
    let rendered;
    try { rendered = marked.parse(text); }
    catch { rendered = `<pre style="white-space:pre-wrap">${text}</pre>`; }
    rendered = rendered.replace(/<a\s+href=/gi, '<a target="_blank" rel="noopener noreferrer" href=');
    if (!resolveAsset) return rendered;
    return rendered.replace(/(<img\b[^>]*\bsrc=["'])([^"']+)(["'])/gi, (match, pre, src, post) => {
      if (/^(https?:|data:|blob:|\/)/i.test(src)) return match;
      return pre + resolveAsset(src.replace(/^\.\//, "")).replace(/&/g, "&amp;") + post;
    });
  }, [text, resolveAsset, empty]);
  return <div className={`md ${className}`} dangerouslySetInnerHTML={{ __html: html }} />;
}

/** Read and write one localStorage key. Storage can be unavailable; that is not an error. */
function storedValue(key, fallback) {
  try {
    const value = window.localStorage.getItem(key);
    return value === null ? fallback : value;
  } catch {
    return fallback;
  }
}

function storeValue(key, value) {
  try {
    if (value === null || value === undefined) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, value);
  } catch { /* storage unavailable */ }
}

/** Call ``fn`` every ``ms`` while ``active`` and the tab is visible. */
function usePolling(fn, ms, active) {
  const saved = useRef(fn);
  useEffect(() => { saved.current = fn; }, [fn]);
  useEffect(() => {
    if (!active) return undefined;
    const id = window.setInterval(() => {
      if (document.visibilityState === "visible") saved.current();
    }, ms);
    return () => window.clearInterval(id);
  }, [ms, active]);
}

function useDebounced(value, ms) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const id = window.setTimeout(() => setDebounced(value), ms);
    return () => window.clearTimeout(id);
  }, [value, ms]);
  return debounced;
}

/**
 * The page of a list. Any change to ``listKey`` (search, filter, sort, folder)
 * starts again at page 1. Changing pages scrolls back to the top.
 */
function usePage(listKey) {
  const [state, setState] = useState({ key: listKey, page: 1 });
  const page = state.key === listKey ? state.page : 1;
  const goPage = (n) => {
    setState({ key: listKey, page: n });
    document.querySelector("main")?.scrollTo({ top: 0 });
  };
  return [page, goPage];
}

/** Page numbers to show: the first, the last, and two either side of the current one. */
function pageWindow(page, pages) {
  const shown = new Set([1, pages]);
  for (let n = page - 2; n <= page + 2; n += 1) if (n >= 1 && n <= pages) shown.add(n);
  const sorted = [...shown].sort((a, b) => a - b);
  const items = [];
  sorted.forEach((n, i) => {
    if (i > 0 && n - sorted[i - 1] > 1) items.push(`gap-${n}`);
    items.push(n);
  });
  return items;
}

function Pagination({ page, pageSize, total, onPage }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const first = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const last = Math.min(page * pageSize, total);
  const button = "min-w-[2rem] h-8 px-2 rounded-lg text-xs font-semibold border transition-colors disabled:opacity-40 disabled:cursor-not-allowed";
  return (
    <div className="flex items-center justify-between flex-wrap gap-3 mt-4">
      <span className="text-xs text-slate-500 tabular-nums">
        {formatCount(first)}–{formatCount(last)} of {formatCount(total)}
      </span>
      {pages > 1 && (
        <nav className="flex items-center gap-1" aria-label="Pages">
          <button className={`${button} bg-white border-slate-200 text-slate-600 hover:bg-slate-50`} disabled={page === 1} onClick={() => onPage(page - 1)} aria-label="Previous page">
            ‹
          </button>
          {pageWindow(page, pages).map((n) =>
            typeof n === "string" ? (
              <span key={n} className="px-1 text-xs text-slate-400">…</span>
            ) : (
              <button
                key={n}
                onClick={() => onPage(n)}
                aria-current={n === page ? "page" : undefined}
                className={`${button} tabular-nums ${
                  n === page ? "bg-slate-800 border-slate-800 text-white" : "bg-white border-slate-200 text-slate-600 hover:bg-slate-50"
                }`}
              >
                {n}
              </button>
            )
          )}
          <button className={`${button} bg-white border-slate-200 text-slate-600 hover:bg-slate-50`} disabled={page === pages} onClick={() => onPage(page + 1)} aria-label="Next page">
            ›
          </button>
        </nav>
      )}
    </div>
  );
}
