"use strict";
/* Review: files of a project waiting for a human decision, and the review form. */
(function () {
  function reviewPath(row, suffix) {
    return `/reviews/${encodeURIComponent(row.project)}/${encodeURIComponent(row.filename)}${suffix || ""}`;
  }

  function reviewAssetUrl(row, relative) {
    const url = new URL(API_BASE + reviewPath(row, `/assets/${encodePath(relative)}`));
    url.searchParams.set("engine", row.engine);
    return url.toString();
  }

  function layoutUrl(row) {
    const url = new URL(API_BASE + reviewPath(row, "/layout"));
    url.searchParams.set("engine", row.engine);
    return url.toString();
  }

  function needsReview(row) {
    return row.approved === null || row.approved === undefined;
  }

  function PendingList({ project, rows, onOpen, notice, error, onDismissError, onDismissNotice, onRefresh, loading }) {
    const [query, setQuery] = useState("");
    const shown = useMemo(() => {
      const q = query.trim().toLowerCase();
      return q ? rows.filter((row) => `${row.filename} ${row.engine}`.toLowerCase().includes(q)) : rows;
    }, [rows, query]);

    return (
      <Card>
        <CardHeader
          icon="📝"
          title="Waiting for review"
          subtitle={<span><span className="font-mono">{project}</span> · {plural(rows.length, "file")} to review</span>}
          tone="from-amber-50 to-orange-50"
        >
          <Button onClick={onRefresh} disabled={loading}>Refresh</Button>
        </CardHeader>

        <div className="p-6 sm:p-8">
          <Alert type="success" msg={notice} onClose={onDismissNotice} />
          <Alert type="error" msg={error} onClose={onDismissError} />

          {rows.length > 0 && (
            <div className="flex mb-4">
              <SearchInput value={query} onChange={setQuery} placeholder="Filter by file name or engine" />
            </div>
          )}

          {loading && rows.length === 0 ? (
            <div className="py-16 flex justify-center"><Spinner size={32} /></div>
          ) : shown.length === 0 ? (
            <EmptyState icon={rows.length ? "🔎" : "✅"} title={rows.length ? "No files match that filter." : "Nothing is waiting for review."}>
              {!rows.length && "Extracted files appear here once dataprep has parsed them."}
            </EmptyState>
          ) : (
            <div className="overflow-x-auto border border-slate-200 rounded-xl">
              <table className="w-full text-sm">
                <thead className="bg-slate-50 text-left text-[11px] uppercase tracking-wide text-slate-500">
                  <tr>
                    <th className="px-4 py-3 font-semibold">File</th>
                    <th className="px-4 py-3 font-semibold">Engine</th>
                    <th className="px-4 py-3 font-semibold text-right">Tokens</th>
                    <th className="px-4 py-3 font-semibold">Extracted</th>
                    <th className="px-4 py-3" />
                  </tr>
                </thead>
                <tbody>
                  {shown.map((row) => (
                    <tr
                      key={`${row.filename}/${row.engine}`}
                      role="button"
                      tabIndex={0}
                      onClick={() => onOpen(row)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onOpen(row); }
                      }}
                      className="border-t border-slate-100 cursor-pointer hover:bg-amber-50/60"
                    >
                      <td className="px-4 py-3 font-medium text-slate-800 break-all">{row.filename}</td>
                      <td className="px-4 py-3 font-mono text-xs text-slate-500">{row.engine}</td>
                      <td className="px-4 py-3 text-right text-slate-600 tabular-nums">{formatCount(row.token_num)}</td>
                      <td className="px-4 py-3 text-slate-500 whitespace-nowrap">{formatWhen(row.create_time)}</td>
                      <td className="px-4 py-3 text-right text-xs font-semibold text-blue-600 whitespace-nowrap">Review →</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </Card>
    );
  }

  function LayoutPdf({ src }) {
    return (
      <div>
        <div className="flex items-center justify-between mb-2.5 gap-3">
          <div>
            <span className="text-sm font-semibold text-slate-700">Layout reference</span>
            <p className="text-xs text-slate-500 mt-0.5 leading-relaxed">
              Detected regions on the original pages. Compare them with the extracted text before accepting the parse.
            </p>
          </div>
          <a href={src} target="_blank" rel="noreferrer" className="flex-shrink-0 text-xs font-semibold text-blue-600 hover:text-blue-700">
            Open PDF
          </a>
        </div>
        <iframe title="MinerU layout PDF" src={src} className="review-pane w-full rounded-xl border border-slate-200 bg-slate-100" />
      </div>
    );
  }

  function ContentReview({ req, onSubmit, onBack }) {
    const [view, setView] = useState("preview");
    const [text, setText] = useState(req.content || "");
    const [approved, setApproved] = useState(true);
    const [chunking, setChunking] = useState(false);
    const [err, setErr] = useState("");
    const resolveAsset = useCallback((src) => reviewAssetUrl(req, src), [req]);

    const effectiveApproved = req.permit_reject ? approved : true;

    const handleSubmit = async () => {
      setErr("");
      if (effectiveApproved && !text.trim()) {
        setErr("Cannot approve empty content. Please add text or switch to Reject.");
        return;
      }
      try {
        await onSubmit({
          approved: effectiveApproved,
          text: effectiveApproved ? text : null,
          requireChunking: effectiveApproved && (chunking || !req.ask_chunking),
          createTime: req.create_time,
        });
      } catch (e) {
        setErr(e.message);
      }
    };

    return (
      <Card>
        <div className="px-6 sm:px-8 py-6 border-b border-slate-100 bg-gradient-to-br from-green-50 to-emerald-50">
          <div className="flex items-start justify-between gap-4">
            <div className="flex items-center gap-3 min-w-0">
              <button
                onClick={onBack}
                className="w-8 h-8 flex-shrink-0 rounded-lg border border-slate-200 bg-white text-slate-500 hover:text-slate-800"
                title="Back to files"
              >
                ←
              </button>
              <div className="min-w-0">
                <h2 className="text-xl font-bold text-slate-800">Content Review</h2>
                <p className="text-slate-500 text-sm mt-0.5">
                  {req.has_layout
                    ? "Compare the layout reference with the extracted text, then approve or reject the parse."
                    : "Review the extracted text. You may edit it before approving."}
                </p>
                <p className="text-xs text-slate-400 mt-1 font-mono break-all">
                  {req.project} / {req.filename} · {req.engine}
                </p>
              </div>
            </div>
            {req.token_num != null && (
              <span className="flex-shrink-0 px-3 py-1 bg-white border border-blue-200 text-blue-700 text-xs font-bold rounded-full shadow-sm">
                ~{formatCount(req.token_num)} tokens
              </span>
            )}
          </div>
        </div>

        <div className="p-6 sm:p-8 space-y-6">
          <Alert type="error" msg={err} onClose={() => setErr("")} />

          <div className={req.has_layout ? "grid grid-cols-1 lg:grid-cols-2 gap-6 items-start" : ""}>
            {req.has_layout && <LayoutPdf src={layoutUrl(req)} />}

            <div>
              <div className="flex items-center justify-between mb-2.5">
                <span className="text-sm font-semibold text-slate-700">Extracted Content</span>
                <div className="flex border border-slate-200 rounded-lg overflow-hidden text-xs font-semibold">
                  {["preview", "edit"].map((m) => (
                    <button
                      key={m}
                      onClick={() => setView(m)}
                      className={`px-3.5 py-1.5 transition-colors ${
                        view === m ? "bg-slate-800 text-white" : "bg-white text-slate-500 hover:bg-slate-50"
                      }`}
                    >
                      {m === "preview" ? "👁 Preview" : "✏️ Edit"}
                    </button>
                  ))}
                </div>
              </div>

              {view === "preview" ? (
                <div className="review-pane p-5 bg-white border border-slate-200 rounded-xl scroll-thin text-sm leading-relaxed">
                  <MdPreview text={text} resolveAsset={resolveAsset} />
                </div>
              ) : (
                <textarea
                  value={text}
                  onChange={(e) => setText(e.target.value)}
                  placeholder="Extracted content will appear here…"
                  className="review-pane w-full px-4 py-3 border border-slate-300 rounded-xl text-sm font-mono leading-relaxed focus:ring-2 focus:ring-blue-500 focus:border-transparent resize-y transition-all"
                />
              )}
            </div>
          </div>

          {!req.permit_reject && (
            <Alert
              type="info"
              msg="This document exceeds the token threshold. It will be automatically processed and chunked — rejection is not available."
            />
          )}

          {req.permit_reject && (
            <div>
              <p className="text-sm font-semibold text-slate-700 mb-3">Your Decision</p>
              <div className="grid grid-cols-2 gap-3">
                <button
                  onClick={() => setApproved(true)}
                  className={`py-4 rounded-xl border-2 font-bold text-sm transition-all ${
                    approved
                      ? "border-green-500 bg-green-50 text-green-700 shadow shadow-green-100"
                      : "border-slate-200 bg-white text-slate-400 hover:border-green-300 hover:text-green-600 hover:bg-green-50"
                  }`}
                >
                  ✓&nbsp; Approve
                </button>
                <button
                  onClick={() => setApproved(false)}
                  className={`py-4 rounded-xl border-2 font-bold text-sm transition-all ${
                    !approved
                      ? "border-red-500 bg-red-50 text-red-700 shadow shadow-red-100"
                      : "border-slate-200 bg-white text-slate-400 hover:border-red-300 hover:text-red-600 hover:bg-red-50"
                  }`}
                >
                  ✗&nbsp; Reject
                </button>
              </div>
            </div>
          )}

          {effectiveApproved && req.ask_chunking && (
            <div className="p-4 bg-amber-50 border border-amber-200 rounded-xl">
              <label className="flex items-start gap-3 cursor-pointer group">
                <input
                  type="checkbox"
                  checked={chunking}
                  onChange={(e) => setChunking(e.target.checked)}
                  className="mt-0.5 w-4 h-4 rounded flex-shrink-0"
                  style={{ accentColor: "#d97706" }}
                />
                <div>
                  <span className="text-sm font-bold text-amber-900 group-hover:text-amber-700 transition-colors">
                    Enable Chunking
                  </span>
                  <p className="text-xs text-amber-700 mt-0.5 leading-relaxed">
                    Splits the approved text into overlapping chunks for improved search precision. Recommended for large documents
                    {req.token_num != null ? ` (this document: ~${formatCount(req.token_num)} tokens)` : ""}.
                  </p>
                </div>
              </label>
            </div>
          )}

          {effectiveApproved && !req.ask_chunking && (
            <Alert type="info" msg="This document exceeds 5,000 tokens. Publishing will split it into chunks." />
          )}

          {req.permit_reject && !approved && (
            <Alert
              type="warn"
              msg="This content will be rejected. The pipeline will fall back to VLM to generate keyword and semantic texts."
            />
          )}

          <button
            onClick={handleSubmit}
            className="w-full py-3.5 bg-blue-600 hover:bg-blue-700 active:scale-[0.99] text-white font-bold rounded-xl transition-all shadow shadow-blue-200 text-sm"
          >
            Submit Review →
          </button>
        </div>
      </Card>
    );
  }

  function ReviewPage({ project, file, onFileChange }) {
    const [rows, setRows] = useState([]);
    const [review, setReview] = useState(null);
    const [loading, setLoading] = useState(false);
    const [overlay, setOverlay] = useState("");
    const [error, setError] = useState("");
    const [notice, setNotice] = useState("");

    const loadRows = useCallback(async () => {
      if (!project) return [];
      setLoading(true);
      try {
        const data = await api("GET", "/reviews", { project });
        const pending = (Array.isArray(data) ? data : []).filter(needsReview);
        setRows(pending);
        setError("");
        return pending;
      } catch (e) {
        setError(e.message);
        return [];
      } finally {
        setLoading(false);
      }
    }, [project]);

    useEffect(() => {
      setRows([]);
      setNotice("");
      loadRows();
    }, [loadRows]);

    // The file in the URL is the open review. Load it once its row is listed.
    useEffect(() => {
      const row = file && rows.find((r) => r.filename === file);
      if (!row) {
        setReview(null);
        return undefined;
      }
      if (review && review.filename === file) return undefined;
      let cancelled = false;
      setOverlay("Opening review…");
      setError("");
      setNotice("");
      api("GET", reviewPath(row), { engine: row.engine })
        .then((detail) => { if (!cancelled) setReview(detail); })
        .catch((e) => { if (!cancelled) { setError(e.message); onFileChange(null); } })
        .finally(() => setOverlay(""));
      return () => { cancelled = true; };
    }, [file, rows]); // eslint-disable-line

    const back = () => onFileChange(null);

    const submitReview = async ({ approved, text, requireChunking, createTime }) => {
      setOverlay("Saving review…");
      try {
        const saved = await api("POST", reviewPath(review), { engine: review.engine }, {
          approved,
          text,
          require_chunking: requireChunking,
          create_time: createTime,
        });
        const decision = saved.approved ? "Approved" : "Rejected";
        const chunking = saved.require_chunking ? "chunking on" : "chunking off";
        setNotice(`${decision} ${saved.filename}. ${formatCount(saved.token_num)} tokens, ${chunking}. It will be published and indexed shortly.`);
        back();
        await loadRows();
      } finally {
        setOverlay("");
      }
    };

    if (!project) return <NeedProject what="files waiting for review" />;

    return (
      <>
        {overlay && <LoadingOverlay msg={overlay} />}
        {review ? (
          <ContentReview
            key={`${review.project}/${review.filename}/${review.engine}/${review.create_time}`}
            req={review}
            onSubmit={submitReview}
            onBack={back}
          />
        ) : (
          <PendingList
            project={project}
            rows={rows}
            onOpen={(row) => onFileChange(row.filename)}
            notice={notice}
            error={error}
            onDismissError={() => setError("")}
            onDismissNotice={() => setNotice("")}
            onRefresh={loadRows}
            loading={loading}
          />
        )}
      </>
    );
  }

  window.ReviewPage = ReviewPage;
})();
