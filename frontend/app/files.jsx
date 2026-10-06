"use strict";
/* Files: browse store/s3/raw, see each file's pipeline state, queue extraction. */
(function () {
  const POLL_MS = 5000;
  const STATE_FILTERS = [
    { key: "all", label: "All" },
    { key: "none", label: "No state" },
    { key: "extracting", label: "Extracting" },
    { key: "failed", label: "Failed" },
    { key: "reviewing", label: "Reviewing" },
    { key: "indexing", label: "Indexing" },
    { key: "ready", label: "Ready" },
  ];
  // Files with no state sort first.
  const STATE_ORDER = { failed: 1, extracting: 2, reviewing: 3, indexing: 4, ready: 5 };

  function useDebounced(value, ms) {
    const [debounced, setDebounced] = useState(value);
    useEffect(() => {
      const id = window.setTimeout(() => setDebounced(value), ms);
      return () => window.clearTimeout(id);
    }, [value, ms]);
    return debounced;
  }

  function firstSegment(path) {
    return (path || "").split("/").filter(Boolean)[0] || null;
  }

  function parentOf(path) {
    const parts = (path || "").split("/").filter(Boolean);
    parts.pop();
    return parts.join("/");
  }

  function Breadcrumb({ path, onNavigate }) {
    const parts = (path || "").split("/").filter(Boolean);
    return (
      <nav className="flex items-center flex-wrap gap-1 text-sm font-mono">
        <span className="text-slate-400">s3://</span>
        <button onClick={() => onNavigate("")} className="text-blue-600 hover:underline font-semibold">raw</button>
        {parts.map((part, i) => {
          const target = parts.slice(0, i + 1).join("/");
          const last = i === parts.length - 1;
          return (
            <React.Fragment key={target}>
              <span className="text-slate-300">/</span>
              {last ? (
                <span className="text-slate-800 font-semibold">{part}</span>
              ) : (
                <button onClick={() => onNavigate(target)} className="text-blue-600 hover:underline">{part}</button>
              )}
            </React.Fragment>
          );
        })}
      </nav>
    );
  }

  function SortHeader({ label, sortKey, sort, onSort, className = "" }) {
    const active = sort.key === sortKey;
    return (
      <th className={`px-4 py-3 font-semibold ${className}`}>
        <button
          onClick={() => onSort(sortKey)}
          className={`inline-flex items-center gap-1 uppercase tracking-wide ${active ? "text-slate-800" : "hover:text-slate-700"}`}
        >
          {label}
          <span className="text-[9px]">{active ? (sort.dir === "asc" ? "▲" : "▼") : ""}</span>
        </button>
      </th>
    );
  }

  function compareEntries(a, b, sort) {
    if (a.kind !== b.kind) return a.kind === "dir" ? -1 : 1;
    let cmp = 0;
    switch (sort.key) {
      case "size": cmp = (a.size || 0) - (b.size || 0); break;
      case "modified": cmp = new Date(a.modified) - new Date(b.modified); break;
      case "type": cmp = String(a.mime_type || "").localeCompare(String(b.mime_type || "")); break;
      case "state": cmp = (STATE_ORDER[a.state] || 0) - (STATE_ORDER[b.state] || 0); break;
      default: cmp = a.path.toLowerCase().localeCompare(b.path.toLowerCase());
    }
    return sort.dir === "asc" ? cmp : -cmp;
  }

  function FilesPage({ project, onProjectChange, onOpen }) {
    const [path, setPath] = useState(project || "");
    const [query, setQuery] = useState("");
    const debouncedQuery = useDebounced(query.trim(), 300);
    const [stateFilter, setStateFilter] = useState("all");
    const [sort, setSort] = useState({ key: "name", dir: "asc" });
    const [listing, setListing] = useState(null);
    const [selected, setSelected] = useState(() => new Set());
    const [loading, setLoading] = useState(false);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState("");
    const [notice, setNotice] = useState("");
    const requestId = useRef(0);

    // The header project and the browsed project follow each other.
    useEffect(() => {
      if (project && firstSegment(path) !== project) setPath(project);
    }, [project]); // eslint-disable-line

    const navigate = (target) => {
      setPath(target);
      setQuery("");
      setSelected(new Set());
      const seg = firstSegment(target);
      if (seg && seg !== project) onProjectChange(seg);
    };

    const load = useCallback(async (quiet) => {
      const id = ++requestId.current;
      if (!quiet) setLoading(true);
      try {
        const data = await api("GET", "/files", { path, q: debouncedQuery });
        if (id !== requestId.current) return;
        setListing(data);
        setError("");
        // Drop selections that can no longer be extracted.
        setSelected((prev) => {
          const allowed = new Set(data.entries.filter((e) => e.extractable).map((e) => e.path));
          const next = new Set([...prev].filter((p) => allowed.has(p)));
          return next.size === prev.size ? prev : next;
        });
      } catch (e) {
        if (id !== requestId.current) return;
        setError(e.message);
        if (!quiet) setListing(null);
      } finally {
        if (id === requestId.current && !quiet) setLoading(false);
      }
    }, [path, debouncedQuery]);

    useEffect(() => { load(false); }, [load]);

    const entries = listing ? listing.entries : [];
    const files = entries.filter((e) => e.kind === "file");
    const live = files.some((e) => e.state === "extracting" || e.state === "indexing");
    usePolling(() => load(true), POLL_MS, live);

    const counts = useMemo(() => {
      const c = { all: files.length, none: 0 };
      for (const f of files) {
        const key = f.state || "none";
        c[key] = (c[key] || 0) + 1;
      }
      return c;
    }, [listing]); // eslint-disable-line

    const shown = useMemo(() => {
      const rows = entries.filter((e) => {
        if (stateFilter === "all") return true;
        if (e.kind === "dir") return false;
        return stateFilter === "none" ? !e.state : e.state === stateFilter;
      });
      return rows.sort((a, b) => compareEntries(a, b, sort));
    }, [listing, stateFilter, sort]); // eslint-disable-line

    const selectable = shown.filter((e) => e.extractable);
    const allSelected = selectable.length > 0 && selectable.every((e) => selected.has(e.path));

    const toggle = (entry) => {
      setSelected((prev) => {
        const next = new Set(prev);
        if (next.has(entry.path)) next.delete(entry.path); else next.add(entry.path);
        return next;
      });
    };

    const toggleAll = () => {
      setSelected((prev) => {
        const next = new Set(prev);
        if (allSelected) selectable.forEach((e) => next.delete(e.path));
        else selectable.forEach((e) => next.add(e.path));
        return next;
      });
    };

    const onSort = (key) => {
      setSort((prev) => ({ key, dir: prev.key === key && prev.dir === "asc" ? "desc" : "asc" }));
    };

    const extract = async () => {
      const chosen = entries.filter((e) => selected.has(e.path));
      if (chosen.length === 0) return;
      setBusy(true);
      setError("");
      setNotice("");
      try {
        const results = await api("POST", "/extractions", null, {
          files: chosen.map((e) => ({ project: e.project, filename: e.name })),
        });
        const queued = results.filter((r) => r.queued);
        const skipped = results.filter((r) => !r.queued);
        if (queued.length) {
          setNotice(`Queued ${plural(queued.length, "file")} for extraction. Dataprep picks them up within about 30 seconds.`);
        }
        if (skipped.length) {
          setError(skipped.map((r) => `${r.project}/${r.filename}: ${r.reason}`).join("\n"));
        }
        setSelected(new Set());
        await load(true);
      } catch (e) {
        setError(e.message);
      } finally {
        setBusy(false);
      }
    };

    const searching = Boolean(debouncedQuery);

    return (
      <Card>
        <CardHeader
          icon="🗄"
          title="Raw files"
          subtitle={<Breadcrumb path={listing ? listing.path : path} onNavigate={navigate} />}
        >
          <Button onClick={() => load(false)} disabled={loading}>Refresh</Button>
        </CardHeader>

        <div className="p-6 sm:p-8">
          <Alert type="success" msg={notice} onClose={() => setNotice("")} />
          <Alert type="error" msg={error} onClose={() => setError("")} />

          <div className="flex flex-wrap items-center gap-3 mb-4">
            <SearchInput
              value={query}
              onChange={setQuery}
              placeholder={`Search file names under raw/${path || ""}`}
            />
          </div>

          <div className="flex flex-wrap gap-1.5 mb-4">
            {STATE_FILTERS.map((f) => {
              const active = stateFilter === f.key;
              const n = counts[f.key] || 0;
              return (
                <button
                  key={f.key}
                  onClick={() => setStateFilter(f.key)}
                  className={`px-3 py-1.5 rounded-full text-xs font-semibold border transition-colors ${
                    active
                      ? "bg-slate-800 text-white border-slate-800"
                      : "bg-white text-slate-600 border-slate-200 hover:bg-slate-50"
                  }`}
                >
                  {f.label}
                  <span className={`ml-1.5 ${active ? "text-slate-300" : "text-slate-400"}`}>{n}</span>
                </button>
              );
            })}
          </div>

          {selected.size > 0 && (
            <div className="sticky top-2 z-[5] mb-4 px-4 py-3 rounded-xl bg-blue-600 text-white flex items-center justify-between gap-3 shadow-lg shadow-blue-200 fade-up">
              <span className="text-sm font-semibold">{plural(selected.size, "file")} selected</span>
              <div className="flex items-center gap-2">
                <button onClick={() => setSelected(new Set())} className="px-3 py-1.5 text-xs font-semibold rounded-lg hover:bg-blue-500">
                  Clear
                </button>
                <button
                  onClick={extract}
                  disabled={busy}
                  className="px-3.5 py-1.5 text-xs font-bold rounded-lg bg-white text-blue-700 hover:bg-blue-50 disabled:opacity-60 inline-flex items-center gap-2"
                >
                  {busy && <Spinner size={12} />}
                  Start extraction →
                </button>
              </div>
            </div>
          )}

          {loading && !listing ? (
            <div className="py-16 flex justify-center"><Spinner size={32} /></div>
          ) : shown.length === 0 ? (
            <EmptyState icon={searching ? "🔎" : "📂"} title={searching ? "No files match that search." : entries.length ? "No files in this state." : "This folder is empty."}>
              {!searching && !path && "Put files under store/s3/raw/{project}/ to see them here."}
            </EmptyState>
          ) : (
            <div className="overflow-x-auto border border-slate-200 rounded-xl">
              <table className="w-full text-sm">
                <thead className="bg-slate-50 text-left text-[11px] text-slate-500">
                  <tr>
                    <th className="pl-4 pr-1 py-3 w-8">
                      <input
                        type="checkbox"
                        aria-label="Select all files that can be extracted"
                        checked={allSelected}
                        disabled={selectable.length === 0}
                        onChange={toggleAll}
                        className="w-4 h-4 rounded accent-blue-600 disabled:opacity-30"
                      />
                    </th>
                    <SortHeader label="Name" sortKey="name" sort={sort} onSort={onSort} />
                    <SortHeader label="Type" sortKey="type" sort={sort} onSort={onSort} className="hidden md:table-cell" />
                    <SortHeader label="Size" sortKey="size" sort={sort} onSort={onSort} className="text-right" />
                    <SortHeader label="Modified" sortKey="modified" sort={sort} onSort={onSort} className="hidden sm:table-cell" />
                    <SortHeader label="State" sortKey="state" sort={sort} onSort={onSort} />
                  </tr>
                </thead>
                <tbody>
                  {!searching && path && stateFilter === "all" && (
                    <tr className="border-t border-slate-100 hover:bg-slate-50 cursor-pointer" onClick={() => navigate(parentOf(path))}>
                      <td />
                      <td className="px-4 py-2.5 text-slate-500" colSpan={5}>
                        <span className="mr-2">↰</span>..
                      </td>
                    </tr>
                  )}
                  {shown.map((entry) => (
                    <FileRow
                      key={entry.path}
                      entry={entry}
                      searching={searching}
                      checked={selected.has(entry.path)}
                      onToggle={() => toggle(entry)}
                      onNavigate={navigate}
                      onOpen={onOpen}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <p className="text-xs text-slate-400 mt-3 leading-relaxed">
            {listing && listing.truncated && <span className="text-amber-600 font-semibold">Showing the first 1,000 matches. Narrow the search to see more. </span>}
            Files at <span className="font-mono">raw/&#123;project&#125;/&#123;file&#125;</span> with no state, or whose extraction failed, can be selected for extraction.
            Supported types: PDF, images (JPG, PNG), and Office documents.
          </p>
        </div>
      </Card>
    );
  }

  function FileRow({ entry, searching, checked, onToggle, onNavigate, onOpen }) {
    const isDir = entry.kind === "dir";
    const parent = parentOf(entry.path);
    const openState = () => {
      if (entry.state === "reviewing") onOpen("review", entry.project, entry.name);
      if (entry.state === "ready") onOpen("ready", entry.project, entry.name);
    };
    const linked = entry.state === "reviewing" || entry.state === "ready";

    return (
      <tr
        className={`border-t border-slate-100 ${checked ? "bg-blue-50/70" : "hover:bg-slate-50"} ${isDir ? "cursor-pointer" : ""}`}
        onClick={isDir ? () => onNavigate(entry.path) : undefined}
      >
        <td className="pl-4 pr-1 py-2.5" onClick={(e) => e.stopPropagation()}>
          {!isDir && (
            <input
              type="checkbox"
              aria-label={`Select ${entry.name}`}
              checked={checked}
              disabled={!entry.extractable}
              onChange={onToggle}
              title={entry.extractable ? "" : entry.supported ? "Only files with no state, or failed, can be extracted." : "This file type or location cannot be extracted."}
              className="w-4 h-4 rounded accent-blue-600 disabled:opacity-25"
            />
          )}
        </td>
        <td className="px-4 py-2.5 min-w-[14rem]">
          <div className="flex items-center gap-2.5 min-w-0">
            <span className="flex-shrink-0">{isDir ? "📁" : "📄"}</span>
            <div className="min-w-0">
              {isDir ? (
                <span className="font-semibold text-slate-800 break-all">{entry.name}</span>
              ) : (
                <a
                  href={`${API_BASE}/files/raw/${encodePath(entry.path)}`}
                  target="_blank"
                  rel="noreferrer"
                  className="font-medium text-slate-800 hover:text-blue-600 hover:underline break-all"
                  title="Open the raw file"
                >
                  {entry.name}
                </a>
              )}
              {searching && parent && <div className="text-[11px] text-slate-400 font-mono truncate">raw/{parent}</div>}
            </div>
          </div>
        </td>
        <td className="px-4 py-2.5 text-xs text-slate-500 font-mono hidden md:table-cell">{isDir ? "folder" : entry.mime_type || "—"}</td>
        <td className="px-4 py-2.5 text-right text-slate-600 whitespace-nowrap tabular-nums">{isDir ? "" : formatSize(entry.size)}</td>
        <td className="px-4 py-2.5 text-slate-500 whitespace-nowrap hidden sm:table-cell">{formatWhen(entry.modified)}</td>
        <td className="px-4 py-2.5">
          {!isDir && (
            linked ? (
              <button onClick={openState} title={entry.state === "reviewing" ? "Open the review" : "Open the document"}>
                <StateBadge state={entry.state} />
              </button>
            ) : (
              <StateBadge state={entry.state} title={entry.error} />
            )
          )}
          {entry.state === "failed" && entry.error && (
            <div className="text-[11px] text-red-600 mt-1 max-w-[16rem] truncate" title={entry.error}>{entry.error}</div>
          )}
        </td>
      </tr>
    );
  }

  window.FilesPage = FilesPage;
})();
