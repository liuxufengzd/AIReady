"use strict";
/* App shell: navigation, the shared project selector, and hash routing.
   Routes look like #/ready?project=acme&file=report.pdf so a view can be linked. */
(function () {
  const PAGES = [
    { key: "files", label: "Files", icon: "🗄" },
    { key: "review", label: "Review", icon: "📝" },
    { key: "ready", label: "Ready", icon: "📚" },
    { key: "assistant", label: "Assistant", icon: "💬" },
  ];
  const PROJECT_KEY = "ai-ready.project";

  function parseHash() {
    const raw = window.location.hash.replace(/^#\/?/, "");
    const [page, query] = raw.split("?");
    const params = new URLSearchParams(query || "");
    return {
      page: PAGES.some((p) => p.key === page) ? page : "files",
      project: params.get("project") || null,
      file: params.get("file") || null,
    };
  }

  function buildHash({ page, project, file }) {
    const params = new URLSearchParams();
    if (project) params.set("project", project);
    if (file) params.set("file", file);
    const query = params.toString();
    return `#/${page}${query ? `?${query}` : ""}`;
  }

  function ProjectSelect({ projects, value, onChange, onRefresh }) {
    const options = value && !projects.includes(value) ? [value, ...projects] : projects;
    return (
      <div className="flex items-center gap-2">
        <label htmlFor="project-select" className="text-[11px] font-semibold uppercase tracking-wide text-slate-400 hidden lg:block">
          Project
        </label>
        <select
          id="project-select"
          value={value || ""}
          onFocus={onRefresh}
          onChange={(e) => onChange(e.target.value || null)}
          className="max-w-[12rem] px-3 py-1.5 border border-slate-300 rounded-lg text-sm bg-white text-slate-700 focus:ring-2 focus:ring-blue-500"
        >
          <option value="">{options.length ? "Select a project" : "No projects"}</option>
          {options.map((p) => <option key={p} value={p}>{p}</option>)}
        </select>
      </div>
    );
  }

  /** The route lives in the URL hash. A hash without a project gets the last one used. */
  function currentRoute() {
    const parsed = parseHash();
    if (parsed.project) return parsed;
    const route = { ...parsed, project: storedValue(PROJECT_KEY, null) };
    window.history.replaceState(null, "", buildHash(route));
    return route;
  }

  function App() {
    const [route, setRouteState] = useState(currentRoute);
    const [projects, setProjects] = useState([]);

    useEffect(() => {
      const onChange = () => setRouteState(currentRoute());
      window.addEventListener("hashchange", onChange);
      return () => window.removeEventListener("hashchange", onChange);
    }, []);

    const setRoute = (patch) => {
      window.location.hash = buildHash({ ...route, ...patch });
    };

    useEffect(() => { storeValue(PROJECT_KEY, route.project); }, [route.project]);

    const loadProjects = useCallback(async () => {
      try {
        const list = await api("GET", "/projects");
        setProjects(Array.isArray(list) ? list : []);
      } catch { /* the pages report connection errors */ }
    }, []);
    useEffect(() => { loadProjects(); }, [loadProjects]);

    // Pick the only project when none is chosen yet.
    useEffect(() => {
      if (!route.project && projects.length === 1) setRoute({ project: projects[0] });
    }, [projects]); // eslint-disable-line

    const changeProject = (project) => {
      if (project !== route.project) setRoute({ project, file: null });
    };
    const goto = (page) => setRoute({ page, file: null });
    const setFile = (file) => setRoute({ file });
    const openFromFiles = (page, project, file) => setRoute({ page, project, file });

    const { page, project, file } = route;
    const fullHeight = page === "assistant";
    // An open review or document shows two panes side by side.
    const width = file ? "max-w-screen-2xl" : "max-w-6xl";

    let content = null;
    if (page === "files") {
      content = <FilesPage project={project} onProjectChange={changeProject} onOpen={openFromFiles} />;
    } else if (page === "review") {
      content = <ReviewPage project={project} file={file} onFileChange={setFile} />;
    } else if (page === "ready") {
      content = <ReadyPage project={project} file={file} onFileChange={setFile} />;
    } else {
      content = <AssistantPage project={project} />;
    }

    return (
      <div className="h-full flex flex-col">
        <header className="bg-white border-b border-slate-200 shadow-sm flex-shrink-0 z-20">
          <div className="px-4 sm:px-6 h-14 flex items-center justify-between gap-4">
            <div className="flex items-center gap-6 min-w-0">
              <a href="#/files" onClick={(e) => { e.preventDefault(); goto("files"); }} className="flex items-center gap-2.5 flex-shrink-0">
                <div className="w-8 h-8 bg-blue-600 rounded-lg grid place-items-center text-white font-black text-xs shadow shadow-blue-200">AI</div>
                <div className="leading-tight hidden sm:block">
                  <p className="text-sm font-bold text-slate-800">AI Ready</p>
                  <p className="text-[10px] text-slate-400">Data console</p>
                </div>
              </a>
              <nav className="flex items-center gap-1 overflow-x-auto">
                {PAGES.map((p) => {
                  const active = p.key === page;
                  return (
                    <a
                      key={p.key}
                      href={buildHash({ page: p.key, project })}
                      onClick={(e) => { e.preventDefault(); goto(p.key); }}
                      aria-current={active ? "page" : undefined}
                      className={`px-3 py-1.5 rounded-lg text-sm font-semibold whitespace-nowrap transition-colors ${
                        active ? "bg-slate-800 text-white" : "text-slate-500 hover:text-slate-800 hover:bg-slate-100"
                      }`}
                    >
                      <span className="mr-1.5">{p.icon}</span>
                      <span className="hidden sm:inline">{p.label}</span>
                    </a>
                  );
                })}
              </nav>
            </div>
            <ProjectSelect projects={projects} value={project} onChange={changeProject} onRefresh={loadProjects} />
          </div>
        </header>

        {fullHeight ? (
          <main className="flex-1 min-h-0 flex">{content}</main>
        ) : (
          <main className="flex-1 min-h-0 scroll-thin">
            <div className={`${width} mx-auto px-4 sm:px-6 py-8`}>{content}</div>
          </main>
        )}
      </div>
    );
  }

  ReactDOM.createRoot(document.getElementById("root")).render(<App />);
})();
