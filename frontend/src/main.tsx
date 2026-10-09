import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";
import { createRoot } from "react-dom/client";
import {
  api,
  download,
  downloadExport,
  errorMessage,
  isTerminal,
  readableBytes,
  type Artifact,
  type Config,
  type ExportStatus,
  type ReportEntry,
  type Resources,
  type Run,
} from "./api";
import "./style.css";

function State({ run }: { run: Run }) {
  const label = run.is_finished_ok ? "Completed" : run.process_state;
  return (
    <span className={`state state-${run.process_state}`}>
      {label || "created"}
    </span>
  );
}

function ExportPanel({ token, uuid, executionId }: { token: string; uuid: string; executionId: string }) {
  const [profile, setProfile] = useState<"science" | "full-state">("science");
  const [status, setStatus] = useState<ExportStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!status || status.state !== "preparing") return;
    let active = true;
    const timer = setInterval(() => {
      void api<ExportStatus>(token, `/exports/${status.export_id}`).then((value) => {
        if (active) setStatus(value);
      }).catch((err) => {
        if (active) setError(errorMessage(err));
      });
    }, 2000);
    return () => { active = false; clearInterval(timer); };
  }, [token, status?.export_id, status?.state]);

  async function prepare() {
    setBusy(true);
    setError("");
    try {
      setStatus(await api<ExportStatus>(token, `/runs/${uuid}/exports`, {
        execution_id: executionId, profile,
      }));
    } catch (err) { setError(errorMessage(err)); }
    finally { setBusy(false); }
  }

  const progress = status?.progress;
  return <div className="export-card">
    <strong><code>{executionId}</code></strong>
    <div className="export-actions">
      <select aria-label={`Export profile for ${executionId}`} value={profile}
        disabled={busy || status?.state === "preparing"}
        onChange={(event) => setProfile(event.target.value as "science" | "full-state")}>
        <option value="science">Science: stored observables and diagnostics</option>
        <option value="full-state">Full state: physics and recovery state</option>
      </select>
      <button disabled={busy || status?.state === "preparing"} onClick={() => void prepare()}>
        {busy ? "Requesting…" : "Prepare archive"}
      </button>
    </div>
    {status?.state === "preparing" && <p role="status" className="muted">
      Preparing archive: {progress?.phase.replaceAll("_", " ")}
      {progress?.total_bytes ? ` · ${readableBytes(progress.completed_bytes ?? 0)} / ${readableBytes(progress.total_bytes)}` : ""}
      <progress value={progress?.completed_bytes} max={progress?.total_bytes || undefined} />
    </p>}
    {status?.state === "failed" && <p role="alert">{status.error}</p>}
    {status?.state === "ready" && status.receipt && <>
      <dl>
        <dt>Archive</dt><dd><code>{status.receipt.filename}</code></dd>
        <dt>Size</dt><dd>{readableBytes(status.receipt.bytes)} ({status.receipt.bytes.toLocaleString()} bytes)</dd>
        <dt>SHA-256</dt><dd><code>{status.receipt.sha256}</code></dd>
        <dt>Snapshot</dt><dd><code>{status.receipt.snapshot_identity}</code></dd>
      </dl>
      <button className="primary" onClick={() => {
        setError("");
        void downloadExport(token, status.export_id).catch((err) => setError(errorMessage(err)));
      }}>Download archive</button>
      <p className="muted">
        Transfer progress and resume are available in your browser's downloads.
        The archive is retained until {new Date(status.expires_unix * 1000).toLocaleString()}.
        If authorization expires, click Download archive again before resuming.
      </p>
      <p className="muted">Successful export verifies transport integrity. Review scientific acceptance and completeness in the archive.</p>
    </>}
    {error && <p role="alert">{error}</p>}
  </div>;
}

function App() {
  const [token, setToken] = useState("");
  const [draftToken, setDraftToken] = useState("");
  const [config, setConfig] = useState<Config | null>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<Run | null>(null);
  const [report, setReport] = useState<ReportEntry[]>([]);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [showSubmit, setShowSubmit] = useState(false);
  const [plan, setPlan] = useState<string | null>(null);
  const [planName, setPlanName] = useState("");
  const refreshSequence = useRef(0);
  const [code, setCode] = useState("");
  const [label, setLabel] = useState("");
  const [resources, setResources] = useState<Resources>({
    num_machines: 1,
    num_mpiprocs_per_machine: 1,
    num_cores_per_mpiproc: 1,
    max_wallclock_seconds: 3600,
    max_memory_kb: 4194304,
  });

  const refresh = useCallback(async () => {
    if (!token) return;
    const sequence = ++refreshSequence.current;
    try {
      const page = await api<{ runs: Run[] }>(
        token,
        `/runs?limit=25&offset=${offset}`,
      );
      if (sequence !== refreshSequence.current) return;
      setRuns(page.runs);
      if (selected) {
        const [run, logs, files] = await Promise.all([
          api<Run>(token, `/runs/${selected}`),
          api<{ entries: ReportEntry[] }>(token, `/runs/${selected}/report`),
          api<{ artifacts: Artifact[] }>(token, `/runs/${selected}/artifacts`),
        ]);
        if (sequence !== refreshSequence.current) return;
        setDetail(run);
        setReport(logs.entries);
        setArtifacts(files.artifacts);
      }
    } catch (err) {
      if (sequence === refreshSequence.current) setError(errorMessage(err));
    }
  }, [token, selected, offset]);

  useEffect(() => {
    void refresh();
    const interval = setInterval(() => {
      if (!document.hidden) void refresh();
    }, 10000);
    return () => {
      clearInterval(interval);
      refreshSequence.current += 1;
    };
  }, [refresh]);

  async function login(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const value = await api<Config>(draftToken, "/config");
      setConfig(value);
      setResources(value.defaults);
      setCode(value.codes[0]);
      setToken(draftToken);
      setDraftToken("");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function readPlan(file: File | undefined) {
    setPlan(null);
    setPlanName("");
    setError("");
    if (!file || !config) return;
    try {
      if (file.size > config.limits.plan_bytes)
        throw new Error("Plan exceeds the portal upload limit.");
      const source = await file.text();
      const value: unknown = JSON.parse(source);
      if (!value || Array.isArray(value) || typeof value !== "object")
        throw new Error("Plan must be a JSON object.");
      setPlan(source);
      setPlanName(file.name);
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!plan) return;
    setBusy(true);
    setError("");
    try {
      const run = await api<Run>(token, "/runs", {
        plan,
        code_uuid: code,
        resources,
        label,
      });
      setSelected(run.uuid);
      setDetail(null);
      setOffset(0);
      setShowSubmit(false);
      setPlan(null);
      setPlanName("");
      setLabel("");
      await refresh();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function cancel() {
    if (
      !detail ||
      !window.confirm(
        "Request cancellation of this workflow and its running calculations?",
      )
    )
      return;
    setBusy(true);
    setError("");
    try {
      await api(token, `/runs/${detail.uuid}/kill`, {});
      await refresh();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  function logout() {
    refreshSequence.current += 1;
    setToken("");
    setConfig(null);
    setRuns([]);
    setSelected(null);
    setDetail(null);
    setArtifacts([]);
    setReport([]);
    setPlan(null);
    setError("");
  }

  return (
    <>
      <header>
        <div>
          <span className="eyebrow">Quantum transport</span>
          <h1>QCL-NEGF</h1>
        </div>
        <div className="header-right">
          <span>AiiDA · Slurm</span>
          {token && <button onClick={logout}>Disconnect</button>}
        </div>
      </header>
      <main>
        {error && (
          <div role="alert" className="error">
            <span>{error}</span>
            <button aria-label="Dismiss error" onClick={() => setError("")}>
              ×
            </button>
          </div>
        )}
        {!token ? (
          <section className="login panel">
            <p className="eyebrow">Scientific workflows</p>
            <h2>Connect to your control host</h2>
            <p>
              Review calculations, submit frozen plans, and inspect the
              resulting evidence.
            </p>
            <form onSubmit={login}>
              <label>
                Access token
                <input
                  type="password"
                  value={draftToken}
                  autoComplete="off"
                  onChange={(event) => setDraftToken(event.target.value)}
                  required
                />
              </label>
              <button className="primary" disabled={busy}>
                {busy ? "Connecting…" : "Connect"}
              </button>
            </form>
            <p className="muted">
              The token stays in this page's memory. Reloading the page
              disconnects your session.
            </p>
          </section>
        ) : (
          <>
            <div className="toolbar">
              <div>
                <h2>Workflows</h2>
                <p className="muted">
                  Execution state and provenance from AiiDA. Refreshes every 10
                  seconds.
                </p>
              </div>
              <div>
                <button
                  onClick={() => {
                    setError("");
                    void refresh();
                  }}
                >
                  Refresh
                </button>{" "}
                <button
                  className="primary"
                  onClick={() => setShowSubmit(!showSubmit)}
                >
                  {showSubmit ? "Close form" : "Submit plan"}
                </button>
              </div>
            </div>
            {showSubmit && config && (
              <section className="panel submission">
                <h3>Submit a frozen scientific plan</h3>
                <p>
                  Generate the JSON plan with <code>qcl-negf plan CONFIG</code>.
                  Slurm allocates the requested resources for each execution.
                </p>
                <form onSubmit={submit}>
                  <div className="form-grid">
                    <label>
                      Plan JSON
                      <input
                        type="file"
                        accept=".json,application/json"
                        onChange={(event) =>
                          void readPlan(event.target.files?.[0])
                        }
                        required
                      />
                    </label>
                    <label>
                      Label
                      <input
                        value={label}
                        maxLength={120}
                        onChange={(event) => setLabel(event.target.value)}
                      />
                    </label>
                    <label className="wide">
                      Approved solver code
                      <select
                        value={code}
                        onChange={(event) => setCode(event.target.value)}
                      >
                        {config.codes.map((uuid) => (
                          <option key={uuid}>{uuid}</option>
                        ))}
                      </select>
                    </label>
                    {(
                      [
                        "num_cores_per_mpiproc",
                        "max_memory_kb",
                        "max_wallclock_seconds",
                      ] as (keyof Resources)[]
                    ).map((key) => (
                      <label key={key}>
                        {
                          {
                            num_machines: "Machines",
                            num_mpiprocs_per_machine: "Processes per machine",
                            num_cores_per_mpiproc: "Cores per process",
                            max_wallclock_seconds: "Wall time (seconds)",
                            max_memory_kb: "Memory (KiB)",
                          }[key]
                        }
                        <input
                          type="number"
                          min={key === "max_wallclock_seconds" ? 60 : 1}
                          max={config.limits[key]}
                          value={resources[key]}
                          onChange={(event) =>
                            setResources({
                              ...resources,
                              [key]: Number(event.target.value),
                            })
                          }
                          required
                        />
                      </label>
                    ))}
                  </div>
                  <div className="form-footer">
                    <span className="muted">
                      {planName || "Choose a frozen plan to continue."}
                    </span>
                    <button className="primary" disabled={busy || !plan}>
                      {busy ? "Submitting…" : "Submit to AiiDA"}
                    </button>
                  </div>
                </form>
              </section>
            )}
            <div className="workspace">
              <section className="panel run-list" aria-label="Workflow list">
                <div className="list-header">
                  <h3>Recent workflows</h3>
                  <span className="muted">
                    {offset + 1}–{offset + runs.length}
                  </span>
                </div>
                {!runs.length && (
                  <p className="empty">No workflows on this page.</p>
                )}
                {runs.map((run) => (
                  <button
                    key={run.uuid}
                    className={`run ${run.uuid === selected ? "selected" : ""}`}
                    onClick={() => {
                      setSelected(run.uuid);
                      setDetail(null);
                      setReport([]);
                      setArtifacts([]);
                    }}
                  >
                    <div>
                      <strong>{run.label || `Workflow ${run.pk}`}</strong>
                      <State run={run} />
                    </div>
                    <span className="muted">
                      {new Date(run.ctime).toLocaleString()}
                    </span>
                    <code>{run.uuid}</code>
                  </button>
                ))}
                <div className="pagination">
                  <button
                    disabled={offset === 0}
                    onClick={() => setOffset(Math.max(0, offset - 25))}
                  >
                    Previous
                  </button>
                  <button
                    disabled={runs.length < 25}
                    onClick={() => setOffset(offset + 25)}
                  >
                    Next
                  </button>
                </div>
              </section>
              <section className="panel details" aria-label="Workflow details">
                {!detail ? (
                  <div className="empty">
                    <h3>
                      {selected ? "Loading workflow…" : "Select a workflow"}
                    </h3>
                    <p>
                      Inspect its state, scientific outputs, retrieved files and
                      process report.
                    </p>
                  </div>
                ) : (
                  <>
                    <div className="detail-heading">
                      <div>
                        <p className="eyebrow">Workflow {detail.pk}</p>
                        <h3>{detail.label || "Scientific plan"}</h3>
                      </div>
                      <State run={detail} />
                    </div>
                    <dl>
                      <dt>UUID</dt>
                      <dd>
                        <code>{detail.uuid}</code>
                      </dd>
                      <dt>Updated</dt>
                      <dd>{new Date(detail.mtime).toLocaleString()}</dd>
                      <dt>Exit status</dt>
                      <dd>{detail.exit_status ?? "Pending"}</dd>
                    </dl>
                    {!isTerminal(detail) && (
                      <button
                        className="danger"
                        disabled={busy}
                        onClick={() => void cancel()}
                      >
                        Request cancellation
                      </button>
                    )}
                    {detail.children && detail.children.length > 0 && (
                      <>
                        <h4>Executions</h4>
                        <div className="executions">
                          <table>
                            <thead>
                              <tr>
                                <th>Execution</th>
                                <th>AiiDA state</th>
                                <th>Exit status</th>
                              </tr>
                            </thead>
                            <tbody>
                              {detail.children.map((child) => (
                                <tr key={child.uuid}>
                                  <td>
                                    <code>{child.execution_id}</code>
                                  </td>
                                  <td>
                                    <State run={child} />
                                  </td>
                                  <td>{child.exit_status ?? "Pending"}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      </>
                    )}
                    <h4>Scientific results</h4>
                    <p className="muted">
                      Process completion and scientific acceptance are separate.
                      Inspect the result status and diagnostics for each
                      execution.
                    </p>
                    {detail.results && Object.keys(detail.results).length ? (
                      <pre className="json">
                        {JSON.stringify(detail.results, null, 2)}
                      </pre>
                    ) : (
                      <p className="empty small">
                        No scientific results have been recorded.
                      </p>
                    )}
                    <h4>Retrieved artifacts</h4>
                    {artifacts.length ? (
                      <ul className="files">
                        {artifacts.map((file) => (
                          <li key={`${file.execution_id}/${file.attempt}/${file.calcjob_uuid}/${file.path}`}>
                            <div>
                              <code>{file.path}</code>
                              <span className="muted">
                                {file.execution_id} · attempt {file.attempt} · {readableBytes(file.size)}
                              </span>
                            </div>
                            {config &&
                            file.size > config.limits.artifact_bytes ? (
                              <span className="muted">
                                Use complete archive below
                              </span>
                            ) : (
                              <button
                                onClick={() => {
                                  setError("");
                                  void download(token, detail.uuid, file).catch(
                                    (err) => setError(errorMessage(err)),
                                  );
                                }}
                              >
                                Download
                              </button>
                            )}
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <p className="empty small">No retrieved files yet.</p>
                    )}
                    {detail.children?.some((child) => child.retrieved_uuid) && (
                      <>
                        <h4>Complete result archives</h4>
                        <p className="muted">One .tar.xz file for each execution and profile. Arrays retain their native scientific values.</p>
                        {detail.children.filter((child) => child.retrieved_uuid).map((child) =>
                          <ExportPanel key={`${detail.uuid}/${child.uuid}`} token={token} uuid={detail.uuid} executionId={child.execution_id} />
                        )}
                      </>
                    )}
                    {detail.children?.some((child) => child.retrieved_uuid) && (
                      <details>
                        <summary>
                          Export complete repositories, including large arrays
                        </summary>
                        <p className="muted">
                          Run on the control host using the configured AiiDA
                          profile. The output directory must not already exist.
                        </p>
                        {detail.children
                          .filter((child) => child.retrieved_uuid)
                          .map((child) => (
                            <div key={child.uuid}>
                              <p className="muted">{child.execution_id}</p>
                              <pre>
                                verdi -p {config?.profile} node repo dump{" "}
                                {child.retrieved_uuid} ./retrieved-run
                              </pre>
                            </div>
                          ))}
                        <p className="muted">
                          Create one verified archive from the result
                          directory:
                        </p>
                        <pre>
                          qcl-negf-results export ./retrieved-run/result
                          ./exports --profile science
                        </pre>
                      </details>
                    )}
                    <h4>Process report</h4>
                    {report.length ? (
                      <ol className="report">
                        {report.map((entry, index) => (
                          <li key={index}>
                            <span className="muted">
                              {new Date(entry.time).toLocaleString()} ·{" "}
                              {entry.level}
                            </span>
                            <pre>{entry.message}</pre>
                          </li>
                        ))}
                      </ol>
                    ) : (
                      <p className="empty small">No report entries.</p>
                    )}
                  </>
                )}
              </section>
            </div>
          </>
        )}
      </main>
      <footer>QCL-NEGF · Reproducible quantum transport calculations</footer>
    </>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
