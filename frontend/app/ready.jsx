"use strict";
/* Ready: published, indexed documents of a project, their markdown and chunks. */
(function () {
  function docPath(project, filename, suffix) {
    return `/documents/${encodeURIComponent(project)}/${encodeURIComponent(filename)}${suffix || ""}`;
  }

  function docUrl(project, filename, suffix) {
    return API_BASE + docPath(project, filename, suffix);
  }

  function ModeBadge({ approved }) {
    return approved ? (
      <span className="px-2 py-0.5 rounded-md text-[11px] font-semibold bg-slate-100 text-slate-600 border border-slate-200 whitespace-nowrap">Parsed markdown</span>
    ) : (
      <span className="px-2 py-0.5 rounded-md text-[11px] font-semibold bg-fuchsia-50 text-fuchsia-700 border border-fuchsia-200 whitespace-nowrap" title="The parse was rejected in review; the vision model described the file instead.">
        Vision model
      </span>
    );
  }

  function Stat({ label, value }) {
    return (
      <div className="px-4 py-3 rounded-xl bg-white border border-slate-200">
        <div className="text-[11px] uppercase tracking-wide font-semibold text-slate-400">{label}</div>
        <div className="text-lg font-bold text-slate-800 tabular-nums mt-0.5">{value}</div>
      </div>
    );
  }

  // ── List ────────────────────────────────────────────────────────────────

  function ReadyList({ project, listing, query, onQuery, onPage, loading, error, notice, onDismissError, onDismissNotice, onRefresh, onOpen }) {
    const docs = listing ? listing.documents : [];
    const totals = listing ? listing.totals : null;
    const searching = Boolean(query.trim());

    return (
      <Card>
        <CardHeader
          icon="📚"
          title="Ready documents"
          subtitle={<span><span className="font-mono">{project}</span> · published and searchable</span>}
          tone="from-emerald-50 to-teal-50"
        >
          <Button onClick={onRefresh} disabled={loading}>Refresh</Button>
        </CardHeader>

        <div className="p-6 sm:p-8">
          <Alert type="success" msg={notice} onClose={onDismissNotice} />
          <Alert type="error" msg={error} onClose={onDismissError} />

          {totals && totals.documents > 0 && (
            <>
              <div className="grid grid-cols-3 gap-3 mb-5">
                <Stat label="Documents" value={formatCount(totals.documents)} />
                <Stat label="Chunks" value={formatCount(totals.chunks)} />
                <Stat label="Reviewed tokens" value={formatCount(totals.tokens)} />
              </div>
              <div className="flex mb-4">
                <SearchInput value={query} onChange={onQuery} placeholder="Search by file name or type" />
              </div>
            </>
          )}

          {loading && !listing ? (
            <div className="py-16 flex justify-center"><Spinner size={32} /></div>
          ) : docs.length === 0 ? (
            <EmptyState icon={searching ? "🔎" : "📭"} title={searching ? "No documents match that search." : "No documents are ready yet."}>
              {!searching && "Documents appear here after review, publish, and indexing finish."}
            </EmptyState>
          ) : (
            <div className={`overflow-x-auto border border-slate-200 rounded-xl transition-opacity ${loading ? "opacity-60" : ""}`}>
              <table className="w-full text-sm">
                <thead className="bg-slate-50 text-left text-[11px] uppercase tracking-wide text-slate-500">
                  <tr>
                    <th className="px-4 py-3 font-semibold">File</th>
                    <th className="px-4 py-3 font-semibold hidden md:table-cell">Type</th>
                    <th className="px-4 py-3 font-semibold text-right">Size</th>
                    <th className="px-4 py-3 font-semibold text-right">Tokens</th>
                    <th className="px-4 py-3 font-semibold text-right">Chunks</th>
                    <th className="px-4 py-3 font-semibold">Indexed from</th>
                    <th className="px-4 py-3 font-semibold hidden lg:table-cell">Reviewed extraction</th>
                  </tr>
                </thead>
                <tbody>
                  {docs.map((doc) => (
                    <tr
                      key={doc.filename}
                      role="button"
                      tabIndex={0}
                      onClick={() => onOpen(doc.filename)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onOpen(doc.filename); }
                      }}
                      className="border-t border-slate-100 cursor-pointer hover:bg-emerald-50/50"
                    >
                      <td className="px-4 py-3 font-medium text-slate-800 break-all">{doc.filename}</td>
                      <td className="px-4 py-3 text-xs text-slate-500 font-mono hidden md:table-cell">{doc.mime_type || "—"}</td>
                      <td className="px-4 py-3 text-right text-slate-600 whitespace-nowrap tabular-nums">{formatSize(doc.size)}</td>
                      <td className="px-4 py-3 text-right text-slate-600 tabular-nums">{doc.token_num >= 0 ? formatCount(doc.token_num) : "—"}</td>
                      <td className="px-4 py-3 text-right text-slate-600 tabular-nums">{formatCount(doc.chunk_count)}</td>
                      <td className="px-4 py-3"><ModeBadge approved={doc.approved} /></td>
                      <td className="px-4 py-3 text-slate-500 whitespace-nowrap hidden lg:table-cell">{formatWhen(doc.create_time)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {listing && listing.total > 0 && (
            <Pagination page={listing.page} pageSize={listing.page_size} total={listing.total} onPage={onPage} />
          )}
        </div>
      </Card>
    );
  }

  // ── Detail ──────────────────────────────────────────────────────────────

  function ChunkText({ label, text, tokens }) {
    const [open, setOpen] = useState(false);
    const long = text.length > 420 || text.split("\n").length > 6;
    return (
      <div>
        {label && (
          <div className="flex items-center justify-between mb-1">
            <span className="text-[11px] uppercase tracking-wide font-semibold text-slate-400">{label}</span>
            <span className="text-[11px] font-semibold text-slate-500 tabular-nums">{formatCount(tokens)} tokens</span>
          </div>
        )}
        <div className={`text-[13px] leading-relaxed text-slate-700 whitespace-pre-wrap break-words ${long && !open ? "clamp-6" : ""}`}>
          {text}
        </div>
        {long && (
          <button onClick={() => setOpen(!open)} className="text-xs font-semibold text-blue-600 hover:text-blue-700 mt-1">
            {open ? "Show less" : "Show more"}
          </button>
        )}
      </div>
    );
  }

  function ChunkCard({ chunk, index }) {
    const split = chunk.semantic_text !== chunk.keyword_text;
    return (
      <div className="p-4 rounded-xl border border-slate-200 bg-white hover:border-slate-300 transition-colors">
        <div className="flex items-center justify-between gap-2 mb-2.5">
          <div className="flex items-center gap-2 min-w-0">
            <span className="w-7 h-6 rounded-md bg-slate-800 text-white text-[11px] font-bold grid place-items-center flex-shrink-0">{index + 1}</span>
            <span className="text-[11px] font-mono text-slate-400 truncate" title={chunk.id}>{chunk.id}</span>
          </div>
          <div className="flex items-center gap-1.5 flex-shrink-0">
            {chunk.retrieve_raw_file && (
              <span className="px-2 py-0.5 rounded-md text-[10px] font-bold bg-fuchsia-50 text-fuchsia-700 border border-fuchsia-200" title="Search returns the original file for this chunk.">
                RAW FILE
              </span>
            )}
            <span className="px-2 py-0.5 rounded-md text-[11px] font-bold bg-blue-50 text-blue-700 border border-blue-200 tabular-nums">
              {split
                ? `${formatCount(chunk.semantic_tokens)} / ${formatCount(chunk.keyword_tokens)} tok`
                : `${formatCount(chunk.semantic_tokens)} tok`}
            </span>
          </div>
        </div>
        {split ? (
          <div className="space-y-3">
            <ChunkText label="Semantic text" text={chunk.semantic_text} tokens={chunk.semantic_tokens} />
            <div className="border-t border-dashed border-slate-200" />
            <ChunkText label="Keyword text" text={chunk.keyword_text} tokens={chunk.keyword_tokens} />
          </div>
        ) : (
          <ChunkText text={chunk.semantic_text} />
        )}
      </div>
    );
  }

  function SourcePreview({ project, filename, sourceName }) {
    const url = docUrl(project, filename, "/source");
    if (/\.pdf$/i.test(sourceName)) {
      return <iframe title="Original document" src={url} className="w-full h-full rounded-xl border border-slate-200 bg-slate-100" />;
    }
    return (
      <div className="w-full h-full rounded-xl border border-slate-200 bg-slate-50 grid place-items-center p-4 overflow-auto">
        <img src={url} alt={filename} className="max-w-full rounded" />
      </div>
    );
  }

  function DocumentView({ project, filename, onBack, onDeleted }) {
    const [doc, setDoc] = useState(null);
    const [chunks, setChunks] = useState(null);
    const [chunkError, setChunkError] = useState("");
    const [error, setError] = useState("");
    const [view, setView] = useState("preview");
    const [chunkQuery, setChunkQuery] = useState("");
    const [confirming, setConfirming] = useState(false);
    const [deleting, setDeleting] = useState(false);
    const resolveAsset = useCallback(
      (src) => docUrl(project, filename, `/assets/${encodePath(src)}`),
      [project, filename]
    );

    const loadChunks = useCallback(async () => {
      setChunks(null);
      setChunkError("");
      try {
        setChunks(await api("GET", docPath(project, filename, "/chunks")));
      } catch (e) {
        setChunkError(e.message);
      }
    }, [project, filename]);

    useEffect(() => {
      let cancelled = false;
      setDoc(null);
      setError("");
      api("GET", docPath(project, filename))
        .then((d) => { if (!cancelled) setDoc(d); })
        .catch((e) => { if (!cancelled) setError(e.message); });
      loadChunks();
      return () => { cancelled = true; };
    }, [project, filename, loadChunks]);

    const shownChunks = useMemo(() => {
      if (!chunks) return [];
      const q = chunkQuery.trim().toLowerCase();
      const indexed = chunks.map((chunk, index) => ({ chunk, index }));
      return q
        ? indexed.filter(({ chunk }) => `${chunk.semantic_text}\n${chunk.keyword_text}`.toLowerCase().includes(q))
        : indexed;
    }, [chunks, chunkQuery]);

    const totalTokens = chunks ? chunks.reduce((n, c) => n + c.keyword_tokens, 0) : null;

    const remove = async () => {
      setDeleting(true);
      try {
        await api("DELETE", docPath(project, filename));
        setConfirming(false);
        onDeleted(filename);
      } catch (e) {
        setConfirming(false);
        setError(e.message);
      } finally {
        setDeleting(false);
      }
    };

    return (
      <Card>
        <div className="px-6 sm:px-8 py-5 border-b border-slate-100 bg-gradient-to-br from-emerald-50 to-teal-50">
          <div className="flex items-start justify-between gap-4 flex-wrap">
            <div className="flex items-center gap-3 min-w-0">
              <button
                onClick={onBack}
                className="w-8 h-8 flex-shrink-0 rounded-lg border border-slate-200 bg-white text-slate-500 hover:text-slate-800"
                title="Back to documents"
              >
                ←
              </button>
              <div className="min-w-0">
                <h2 className="text-xl font-bold text-slate-800 break-all">{filename}</h2>
                <div className="flex items-center flex-wrap gap-2 mt-1 text-xs text-slate-500">
                  <span className="font-mono">{project}</span>
                  {doc && <ModeBadge approved={doc.approved} />}
                  {doc && doc.token_num >= 0 && <span>· {formatCount(doc.token_num)} tokens reviewed</span>}
                  {doc && <span>· extracted {formatWhen(doc.create_time)}</span>}
                </div>
              </div>
            </div>
            <div className="flex items-center gap-2 flex-wrap">
              <a
                href={`${API_BASE}/files/raw/${encodePath(`${project}/${filename}`)}`}
                target="_blank"
                rel="noreferrer"
                className="px-3.5 py-2 text-xs font-semibold rounded-lg border border-slate-200 bg-white text-slate-600 hover:bg-slate-50"
              >
                Raw file ↗
              </a>
              {doc && doc.has_layout && (
                <a
                  href={docUrl(project, filename, "/layout")}
                  target="_blank"
                  rel="noreferrer"
                  className="px-3.5 py-2 text-xs font-semibold rounded-lg border border-slate-200 bg-white text-slate-600 hover:bg-slate-50"
                >
                  Layout ↗
                </a>
              )}
              <Button variant="danger" onClick={() => setConfirming(true)} disabled={!doc}>Delete…</Button>
            </div>
          </div>
        </div>

        <div className="p-6 sm:p-8">
          <Alert type="error" msg={error} onClose={() => setError("")} />

          {!doc && !error ? (
            <div className="py-16 flex justify-center"><Spinner size={32} /></div>
          ) : doc && (
            <div className="grid grid-cols-1 xl:grid-cols-2 gap-6 items-start">
              <section>
                <div className="flex items-center justify-between mb-2.5 gap-3">
                  <span className="text-sm font-semibold text-slate-700">
                    {doc.content !== null ? "Reviewed markdown" : "Original document"}
                  </span>
                  {doc.content !== null && (
                    <div className="flex border border-slate-200 rounded-lg overflow-hidden text-xs font-semibold">
                      {[["preview", "Preview"], ["source", "Markdown"]].map(([m, label]) => (
                        <button
                          key={m}
                          onClick={() => setView(m)}
                          className={`px-3.5 py-1.5 transition-colors ${view === m ? "bg-slate-800 text-white" : "bg-white text-slate-500 hover:bg-slate-50"}`}
                        >
                          {label}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
                {doc.content === null ? (
                  <div className="doc-pane flex flex-col gap-3">
                    <Alert type="info" msg="The extracted markdown was rejected in review, so the vision model described the original file. Its description is in the chunks." />
                    {doc.source_name ? (
                      <div className="flex-1 min-h-0"><SourcePreview project={project} filename={filename} sourceName={doc.source_name} /></div>
                    ) : (
                      <EmptyState icon="📄" title="The original document is not in processed." />
                    )}
                  </div>
                ) : view === "preview" ? (
                  <div className="doc-pane p-5 bg-white border border-slate-200 rounded-xl scroll-thin text-sm leading-relaxed">
                    <MdPreview text={doc.content} resolveAsset={resolveAsset} />
                  </div>
                ) : (
                  <pre className="doc-pane p-5 bg-slate-50 border border-slate-200 rounded-xl scroll-thin text-xs leading-relaxed font-mono whitespace-pre-wrap break-words text-slate-700">
                    {doc.content}
                  </pre>
                )}
              </section>

              <section>
                <div className="flex items-center justify-between mb-2.5 gap-3">
                  <span className="text-sm font-semibold text-slate-700">
                    Chunks{chunks ? ` (${chunks.length})` : ""}
                    {totalTokens !== null && (
                      <span className="ml-2 text-xs font-normal text-slate-400">{formatCount(totalTokens)} keyword tokens in total</span>
                    )}
                  </span>
                </div>
                {chunkError ? (
                  <div>
                    <Alert type="error" msg={chunkError} />
                    <Button onClick={loadChunks}>Try again</Button>
                  </div>
                ) : !chunks ? (
                  <div className="doc-pane grid place-items-center border border-slate-200 rounded-xl bg-slate-50">
                    <div className="flex flex-col items-center gap-3">
                      <Spinner size={28} />
                      <span className="text-xs text-slate-400">Loading chunks and counting tokens…</span>
                    </div>
                  </div>
                ) : (
                  <div className="doc-pane flex flex-col gap-3">
                    {chunks.length > 3 && (
                      <SearchInput value={chunkQuery} onChange={setChunkQuery} placeholder="Find text in chunks" />
                    )}
                    <div className="flex-1 min-h-0 scroll-thin space-y-3 pr-1">
                      {shownChunks.length === 0 ? (
                        <EmptyState icon="🔎" title="No chunk contains that text." />
                      ) : (
                        shownChunks.map(({ chunk, index }) => <ChunkCard key={chunk.id} chunk={chunk} index={index} />)
                      )}
                    </div>
                  </div>
                )}
              </section>
            </div>
          )}
        </div>

        {confirming && (
          <ConfirmDialog
            title={`Delete ${filename}?`}
            confirmLabel={deleting ? "Deleting…" : "Delete document"}
            busy={deleting}
            onCancel={() => setConfirming(false)}
            onConfirm={remove}
          >
            <p>This removes the document from the search index and deletes its processed files: the reviewed markdown, images, layout, and chunks.</p>
            <p>The raw file stays in <span className="font-mono text-xs">raw/{project}/</span> with no state, so it can be extracted again.</p>
          </ConfirmDialog>
        )}
      </Card>
    );
  }

  // ── Page ────────────────────────────────────────────────────────────────

  function ReadyPage({ project, file, onFileChange }) {
    const [listing, setListing] = useState(null);
    const [query, setQuery] = useState("");
    const debouncedQuery = useDebounced(query.trim(), 300);
    const [page, goPage] = usePage([project, debouncedQuery].join("\n"));
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState("");
    const [notice, setNotice] = useState("");
    const requestId = useRef(0);

    const load = useCallback(async () => {
      if (!project) return;
      const id = ++requestId.current;
      setLoading(true);
      try {
        const data = await api("GET", "/documents", { project, q: debouncedQuery, page, page_size: PAGE_SIZE });
        if (id !== requestId.current) return;
        setListing(data);
        setError("");
      } catch (e) {
        if (id === requestId.current) setError(e.message);
      } finally {
        if (id === requestId.current) setLoading(false);
      }
    }, [project, debouncedQuery, page]);

    useEffect(() => { load(); }, [load]);

    useEffect(() => {
      setListing(null);
      setQuery("");
      setNotice("");
    }, [project]);

    if (!project) return <NeedProject what="ready documents" />;

    if (file) {
      return (
        <DocumentView
          key={`${project}/${file}`}
          project={project}
          filename={file}
          onBack={() => onFileChange(null)}
          onDeleted={(name) => {
            setNotice(`Deleted ${name}. Its raw file is back to no state.`);
            onFileChange(null);
            load();
          }}
        />
      );
    }

    return (
      <ReadyList
        project={project}
        listing={listing}
        query={query}
        onQuery={setQuery}
        onPage={goPage}
        loading={loading}
        error={error}
        notice={notice}
        onDismissError={() => setError("")}
        onDismissNotice={() => setNotice("")}
        onRefresh={load}
        onOpen={(name) => onFileChange(name)}
      />
    );
  }

  window.ReadyPage = ReadyPage;
})();
