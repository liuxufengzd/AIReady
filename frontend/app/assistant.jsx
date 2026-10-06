"use strict";
/* Assistant: ask the supervisor about a project's documents. */
(function () {
  const base = SUPERVISOR_BASE;

  const client = {
    listThreads: (project) => api("GET", "/threads", { project }, undefined, base),
    getThread: (id) => api("GET", `/threads/${encodeURIComponent(id)}`, null, undefined, base),
    deleteThread: (id) => api("DELETE", `/threads/${encodeURIComponent(id)}`, null, undefined, base),
  };

  /**
   * POST /query and yield parsed SSE events:
   *   { type: "thread_id", thread_id } · { type: "chunk", text }
   *   { type: "final", text, title }   · { type: "error", message }
   */
  async function* queryStream(body, signal) {
    let resp;
    try {
      resp = await fetch(`${base}/query`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        signal,
      });
    } catch (e) {
      if (e.name === "AbortError") return;
      throw new Error(`Network error — is the supervisor running at ${base}?\n${e.message}`);
    }
    if (!resp.ok) throw new Error(`[${resp.status}] ${await errorDetail(resp)}`);

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split("\n");
        buf = lines.pop(); // keep the last, possibly incomplete, line
        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          const raw = line.slice(6).trim();
          if (!raw) continue;
          try { yield JSON.parse(raw); } catch { /* ignore malformed lines */ }
        }
      }
      buf += decoder.decode();
      if (buf.startsWith("data: ")) {
        const raw = buf.slice(6).trim();
        if (raw) { try { yield JSON.parse(raw); } catch { /* ignore */ } }
      }
    } finally {
      reader.releaseLock();
    }
  }

  function formatDate(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    const diffDays = Math.floor((new Date() - d) / 86400000);
    if (diffDays === 0) return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    if (diffDays === 1) return "Yesterday";
    if (diffDays < 7) return d.toLocaleDateString([], { weekday: "short" });
    return d.toLocaleDateString([], { month: "short", day: "numeric" });
  }

  function Sidebar({ project, threads, currentThreadId, onSelectThread, onNewThread, onDeleteThread, loading }) {
    return (
      <aside className="hidden md:flex flex-col bg-slate-900 border-r border-slate-800" style={{ width: 260, minWidth: 260 }}>
        <div className="px-4 py-4 border-b border-slate-800">
          <p className="text-xs text-slate-500 font-medium">Project</p>
          <p className="text-sm font-bold text-white truncate" title={project}>{project}</p>
        </div>

        <div className="px-3 py-3">
          <button
            onClick={onNewThread}
            className="w-full flex items-center gap-2.5 px-3 py-2.5 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white text-sm font-semibold transition-colors"
          >
            <span className="text-base leading-none">+</span>
            New Conversation
          </button>
        </div>

        <div className="flex-1 sidebar-scroll px-2 pb-4">
          {loading && threads.length === 0 && (
            <div className="flex items-center justify-center py-10">
              <Spinner size={22} color="#6366f1" track="rgba(255,255,255,0.15)" />
            </div>
          )}
          {!loading && threads.length === 0 && (
            <p className="text-center text-slate-600 text-xs py-8 px-2">
              No conversations yet.<br />Ask a question to start one.
            </p>
          )}
          <div className="space-y-0.5">
            {threads.map((t) => {
              const active = t.thread_id === currentThreadId;
              return (
                <div key={t.thread_id} className={`group flex items-center rounded-xl transition-colors ${active ? "bg-slate-700" : "hover:bg-slate-800"}`}>
                  <button className="flex-1 px-3 py-2.5 text-left min-w-0" onClick={() => onSelectThread(t.thread_id)}>
                    <p className={`text-sm truncate leading-snug ${active ? "text-white font-semibold" : "text-slate-300"}`}>
                      {t.title || "Untitled conversation"}
                    </p>
                    <p className="text-[11px] text-slate-500 mt-0.5">{formatDate(t.updated_at)}</p>
                  </button>
                  <button
                    onClick={(e) => { e.stopPropagation(); onDeleteThread(t.thread_id); }}
                    title="Delete"
                    className="flex-shrink-0 opacity-0 group-hover:opacity-100 focus:opacity-100 w-7 h-7 mr-1.5 rounded-lg hover:bg-red-500/20 text-slate-500 hover:text-red-400 flex items-center justify-center text-xs transition-all"
                  >
                    ✕
                  </button>
                </div>
              );
            })}
          </div>
        </div>
      </aside>
    );
  }

  function UserMessage({ text }) {
    return (
      <div className="flex justify-end">
        <div className="max-w-[72%] px-4 py-3 bg-indigo-600 text-white rounded-2xl rounded-br-sm text-sm leading-relaxed whitespace-pre-wrap shadow-sm">
          {text}
        </div>
      </div>
    );
  }

  function AssistantMessage({ text, isStreaming }) {
    const hasText = text && text.trim().length > 0;
    return (
      <div className="flex gap-3">
        <div className="w-7 h-7 bg-emerald-600 rounded-lg flex-shrink-0 grid place-items-center text-white text-xs font-bold mt-0.5 shadow-sm">
          AI
        </div>
        <div className="flex-1 min-w-0 px-4 py-3 bg-white border border-slate-200 rounded-2xl rounded-tl-sm shadow-sm">
          {isStreaming && !hasText ? (
            <div className="flex items-center gap-2 text-slate-400 text-sm">
              <Spinner size={16} color="#94a3b8" />
              <span>Thinking…</span>
            </div>
          ) : (
            <div className="relative">
              <MdPreview text={text} className="md-chat text-sm leading-relaxed" empty="" />
              {isStreaming && <span className="stream-cursor" />}
            </div>
          )}
        </div>
      </div>
    );
  }

  function ChatInput({ onSend, loading }) {
    const [text, setText] = useState("");
    const textareaRef = useRef(null);

    useEffect(() => { if (!loading) textareaRef.current?.focus(); }, [loading]);

    useEffect(() => {
      const el = textareaRef.current;
      if (!el) return;
      el.style.height = "auto";
      el.style.height = Math.min(el.scrollHeight, 200) + "px";
    }, [text]);

    const submit = () => {
      const q = text.trim();
      if (!q || loading) return;
      onSend(q);
      setText("");
    };

    return (
      <div className="px-4 pb-5 pt-3 bg-white border-t border-slate-200">
        <div className="max-w-3xl mx-auto flex items-end gap-3 px-4 py-3 border rounded-2xl transition-all bg-white border-slate-300 focus-within:border-indigo-400 focus-within:ring-2 focus-within:ring-indigo-100">
          <textarea
            ref={textareaRef}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); submit(); }
            }}
            placeholder="Ask a question… (Enter to send, Shift+Enter for newline)"
            rows={1}
            disabled={loading}
            className="flex-1 bg-transparent text-sm text-slate-800 placeholder-slate-400 leading-relaxed resize-none disabled:cursor-not-allowed"
            style={{ maxHeight: 200, overflowY: "auto" }}
          />
          <button
            onClick={submit}
            disabled={!text.trim() || loading}
            className={`flex-shrink-0 w-9 h-9 rounded-xl flex items-center justify-center font-bold text-sm transition-all ${
              !text.trim() || loading
                ? "bg-slate-100 text-slate-400 cursor-not-allowed"
                : "bg-indigo-600 hover:bg-indigo-500 text-white shadow shadow-indigo-200"
            }`}
          >
            {loading ? <Spinner size={16} color="#6366f1" /> : "↑"}
          </button>
        </div>
        <p className="text-center text-[11px] text-slate-400 mt-2">
          Supervisor may make mistakes. Verify important information.
        </p>
      </div>
    );
  }

  function ChatView({ thread, pendingQuery, streamingText, onSend, loading, project }) {
    const bottomRef = useRef(null);

    useEffect(() => {
      bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }, [thread?.body?.length, pendingQuery, streamingText]);

    const isEmpty = !thread || thread.body.length === 0;
    const noThread = !thread && !pendingQuery;

    return (
      <div className="flex-1 flex flex-col min-h-0 bg-slate-50">
        {thread && (
          <div className="px-6 py-4 bg-white border-b border-slate-200 flex-shrink-0">
            <p className="text-base font-bold text-slate-800 truncate">{thread.title || "New Conversation"}</p>
            <p className="text-xs text-slate-400 mt-0.5">
              <span className="font-mono bg-slate-100 px-1.5 py-0.5 rounded text-slate-500">{project}</span>
              {thread.body.length > 0 && (
                <span className="ml-2">{plural(thread.body.length, "exchange")}</span>
              )}
            </p>
          </div>
        )}

        <div className="flex-1 scroll-thin px-4 py-6">
          <div className="max-w-3xl mx-auto space-y-6">
            {noThread && (
              <div className="text-center py-20">
                <div className="w-14 h-14 bg-indigo-100 rounded-2xl grid place-items-center text-indigo-600 text-2xl mx-auto mb-4">💬</div>
                <h3 className="text-lg font-bold text-slate-700 mb-2">Ask about {project}</h3>
                <p className="text-sm text-slate-400">The answer cites the ready documents of this project.</p>
              </div>
            )}

            {isEmpty && thread && !pendingQuery && (
              <div className="text-center py-16">
                <p className="text-slate-400 text-sm">This conversation is empty. Ask a question to begin.</p>
              </div>
            )}

            {thread?.body.map((qa, i) => (
              <div key={i} className="space-y-3 fade-up">
                <UserMessage text={qa.query} />
                <AssistantMessage text={qa.answer} />
              </div>
            ))}

            {pendingQuery && (
              <div className="space-y-3 fade-up">
                <UserMessage text={pendingQuery} />
                <AssistantMessage text={streamingText} isStreaming={loading} />
              </div>
            )}

            <div ref={bottomRef} />
          </div>
        </div>

        <ChatInput onSend={onSend} loading={loading} />
      </div>
    );
  }

  function AssistantPage({ project }) {
    const [threads, setThreads] = useState([]);
    const [threadsLoading, setThreadsLoading] = useState(false);
    const [currentThread, setCurrentThread] = useState(null);
    const [currentId, setCurrentId] = useState(null);
    const [pendingQuery, setPendingQuery] = useState(null);
    const [streamingText, setStreamingText] = useState("");
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState("");
    const abortRef = useRef(null);

    const loadThreads = useCallback(async (proj) => {
      setThreadsLoading(true);
      try {
        setThreads(await client.listThreads(proj));
      } catch (e) {
        setError(`Failed to load threads: ${e.message}`);
      } finally {
        setThreadsLoading(false);
      }
    }, []);

    const startNewThread = useCallback(() => {
      abortRef.current?.abort();
      setCurrentId(null);
      setCurrentThread(null);
      setPendingQuery(null);
      setStreamingText("");
      setLoading(false);
    }, []);

    // A new project starts with its own thread list.
    useEffect(() => {
      startNewThread();
      setThreads([]);
      setError("");
      if (project) loadThreads(project);
    }, [project]); // eslint-disable-line

    useEffect(() => () => abortRef.current?.abort(), []);

    const selectThread = useCallback(async (id) => {
      if (id === currentId) return;
      abortRef.current?.abort();
      setLoading(false);
      setCurrentId(id);
      setCurrentThread(null);
      setPendingQuery(null);
      try {
        setCurrentThread(await client.getThread(id));
      } catch (e) {
        setError(`Failed to load thread: ${e.message}`);
      }
    }, [currentId]);

    const deleteThread = async (id) => {
      try {
        await client.deleteThread(id);
        setThreads((prev) => prev.filter((t) => t.thread_id !== id));
        if (id === currentId) startNewThread();
      } catch (e) {
        setError(`Failed to delete thread: ${e.message}`);
      }
    };

    const sendQuery = useCallback(async (queryText) => {
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      setError("");
      setLoading(true);
      setPendingQuery(queryText);
      setStreamingText("");
      if (!currentThread) setCurrentThread({ thread_id: null, project, title: null, body: [] });

      // The thread ID of a new conversation arrives in the first event.
      let resolvedThreadId = currentId;
      try {
        for await (const event of queryStream(
          { project, query: queryText, thread_id: currentId || undefined },
          controller.signal
        )) {
          if (event.type === "thread_id") {
            resolvedThreadId = event.thread_id;
            setCurrentId(event.thread_id);
          } else if (event.type === "chunk") {
            setStreamingText((prev) => prev + event.text);
          } else if (event.type === "final") {
            const pair = { query: queryText, answer: event.text, tokens: 0 };
            setCurrentThread((prev) => ({
              thread_id: resolvedThreadId,
              project,
              title: event.title || prev?.title,
              body: prev ? [...prev.body, pair] : [pair],
            }));
            setCurrentId(resolvedThreadId);
            setPendingQuery(null);
            setStreamingText("");
            await loadThreads(project);
          } else if (event.type === "error") {
            throw new Error(event.message);
          }
        }
      } catch (e) {
        if (e.name === "AbortError") return;
        setError(`Query failed: ${e.message}`);
        setPendingQuery(null);
        setStreamingText("");
        if (!currentId) setCurrentThread(null);
      } finally {
        if (abortRef.current === controller) setLoading(false);
      }
    }, [project, currentId, currentThread, loadThreads]);

    if (!project) {
      return (
        <div className="p-6 sm:p-10 max-w-5xl mx-auto w-full">
          <NeedProject what="conversations" />
        </div>
      );
    }

    return (
      <div className="flex flex-1 min-h-0">
        <Sidebar
          project={project}
          threads={threads}
          currentThreadId={currentId}
          onSelectThread={selectThread}
          onNewThread={startNewThread}
          onDeleteThread={deleteThread}
          loading={threadsLoading}
        />
        <div className="flex-1 flex flex-col min-w-0">
          {error && (
            <div className="flex-shrink-0 px-6 py-3 bg-red-50 border-b border-red-200 flex items-center gap-3">
              <span className="text-red-500 font-bold text-sm">⚠</span>
              <p className="flex-1 text-red-700 text-sm whitespace-pre-wrap">{error}</p>
              <button onClick={() => setError("")} className="text-red-400 hover:text-red-600 text-xs font-bold">✕</button>
            </div>
          )}
          <ChatView
            thread={currentThread}
            pendingQuery={pendingQuery}
            streamingText={streamingText}
            onSend={sendQuery}
            loading={loading}
            project={project}
          />
        </div>
      </div>
    );
  }

  window.AssistantPage = AssistantPage;
})();
