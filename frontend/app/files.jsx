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
  ];

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

  function FilesPage({ project, onProjectChange, onOpen }) {
    const [path, setPath] = useState(project || "");
    const [query, setQuery] = useState("");
    const debouncedQuery = useDebounced(query.trim(), 300);
    const [stateFilter, setStateFilter] = useState("all");
    const [sort, setSort] = useState({ key: "name", dir: "asc" });
    const [listing, setListing] = useState(null);
    // path -> { project, filename }. Kept across pages.
    const [selected, setSelected] = useState(() => new Map());
    const [loading, setLoading] = useState(false);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState("");
    const [notice, setNotice] = useState("");
    const requestId = useRef(0);
    const [page, goPage] = usePage([path, debouncedQuery, stateFilter, sort.key, sort.dir].join("\n"));

    // The header project and the browsed project follow each other.
    useEffect(() => {
      if (project && firstSegment(path) !== project) setPath(project);
    }, [project]); // eslint-disable-line

    const navigate = (target) => {
      setPath(target);
      setQuery("");
      setSelected(new Map());
      const seg = firstSegment(target);
      if (seg && seg !== project) onProjectChange(seg);
    };

    const load = useCallback(async (quiet) => {
      const id = ++requestId.current;
      if (!quiet) setLoading(true);
      try {
        const data = await api("GET", "/files", {
          path,
          q: debouncedQuery,
          state: stateFilter,
          sort: sort.key,
          order: sort.dir,
          page,
          page_size: PAGE_SIZE,
        });
        if (id !== requestId.current) return;
        setListing(data);
        setError("");
        // Drop selections on this page that can no longer be extracted.
        setSelected((prev) => {
          const stale = data.entries.filter((e) => !e.extractable && prev.has(e.path));
          if (stale.length === 0) return prev;
          const next = new Map(prev);
          stale.forEach((e) => next.delete(e.path));
          return next;
        });
      } catch (e) {
        if (id !== requestId.current) return;
        setError(e.message);
        if (!quiet) setListing(null);
      } finally {
        if (id === requestId.current && !quiet) setLoading(false);
      }
    }, [path, debouncedQuery, stateFilter, sort, page]);

    useEffect(() => { load(false); }, [load]);

    const entries = listing ? listing.entries : [];
    const counts = listing ? listing.counts : {};
    const live = Boolean(counts.extracting || counts.indexing);
    usePolling(() => load(true), POLL_MS, live);

    const selectable = entries.filter((e) => e.extractable);
    const allSelected = selectable.length > 0 && selectable.every((e) => selected.has(e.path));

    const toggle = (entry) => {
      setSelected((prev) => {
        const next = new Map(prev);
        if (next.has(entry.path)) next.delete(entry.path);
        else next.set(entry.path, { project: entry.project, filename: entry.name });
        return next;
      });
    };

    const toggleAll = () => {
      setSelected((prev) => {
        const next = new Map(prev);
        if (allSelected) selectable.forEach((e) => next.delete(e.path));
        else selectable.forEach((e) => next.set(e.path, { project: e.project, filename: e.name }));
        return next;
      });
    };

    const onSort = (key) => {
      setSort((prev) => ({ key, dir: prev.key === key && prev.dir === "asc" ? "desc" : "asc" }));
    };

    const extract = async () => {
      if (selected.size === 0) return;
      setBusy(true);
      setError("");
      setNotice("");
      try {
        const results = await api("POST", "/extractions", null, { files: [...selected.values()] });
        const queued = results.filter((r) => r.queued);
        const skipped = results.filter((r) => !r.queued);
        if (queued.length) {
          setNotice(`Queued ${plural(queued.length, "file")} for extraction. Dataprep picks them up within about 30 seconds.`);
        }
        if (skipped.length) {
          setError(skipped.map((r) => `${r.project}/${r.filename}: ${r.reason}`).join("\n"));
        }
        setSelected(new Map());
        await load(true);
      } catch (e) {
        setError(e.message);
      } finally {
        setBusy(false);
      }
    };

    const searching = Boolean(debouncedQuery);
    const currentPage = listing ? listing.page : page;

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
                  <span className={`ml-1.5 ${active ? "text-slate-300" : "text-slate-400"}`}>{formatCount(n)}</span>
                </button>
              );
            })}
          </div>

          {selected.size > 0 && (
            <div className="sticky top-2 z-[5] mb-4 px-4 py-3 rounded-xl bg-blue-600 text-white flex items-center justify-between gap-3 shadow-lg shadow-blue-200 fade-up">
              <span className="text-sm font-semibold">{plural(selected.size, "file")} selected</span>
              <div className="flex items-center gap-2">
                <button onClick={() => setSelected(new Map())} className="px-3 py-1.5 text-xs font-semibold rounded-lg hover:bg-blue-500">
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
          ) : entries.length === 0 ? (
            <EmptyState icon={searching ? "🔎" : "📂"} title={searching ? "No files match that search." : counts.all ? "No files in this state." : "This folder is empty."}>
              {!searching && !path && "Put files under store/s3/raw/{project}/ to see them here."}
            </EmptyState>
          ) : (
            <div className={`overflow-x-auto border border-slate-200 rounded-xl transition-opacity ${loading ? "opacity-60" : ""}`}>
              <table className="w-full text-sm">
                <thead className="bg-slate-50 text-left text-[11px] text-slate-500">
                  <tr>
                    <th className="pl-4 pr-1 py-3 w-8">
                      <input
                        type="checkbox"
                        aria-label="Select every file on this page that can be extracted"
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
                  {!searching && path && stateFilter === "all" && currentPage === 1 && (
                    <tr className="border-t border-slate-100 hover:bg-slate-50 cursor-pointer" onClick={() => navigate(parentOf(path))}>
                      <td />
                      <td className="px-4 py-2.5 text-slate-500" colSpan={5}>
                        <span className="mr-2">↰</span>..
                      </td>
                    </tr>
                  )}
                  {entries.map((entry) => (
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

          {listing && listing.total > 0 && (
            <Pagination page={listing.page} pageSize={listing.page_size} total={listing.total} onPage={goPage} />
          )}

          <p className="text-xs text-slate-400 mt-3 leading-relaxed">
            Files at <span className="font-mono">raw/&#123;project&#125;/&#123;file&#125;</span> with no state, or whose extraction failed, can be selected for extraction.
            Selections are kept when you change pages.
            Ready files are listed on the Ready page.
            Supported types: PDF, images (JPG, PNG), and Office documents.
          </p>
        </div>
      </Card>
    );
  }

  function FileRow({ entry, searching, checked, onToggle, onNavigate, onOpen }) {
    const isDir = entry.kind === "dir";
    const parent = parentOf(entry.path);
    const openState = () => onOpen("review", entry.project, entry.name);
    const linked = entry.state === "reviewing";

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
              <button onClick={openState} title="Open the review">
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
