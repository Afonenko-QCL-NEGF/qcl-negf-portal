import {useEffect, useRef, useState} from 'react';
import {ApiError, errorMessage, RecoveryRequests,
  type RecoveryExecution, type RecoveryExecutionDetail} from './api.ts';

export function RecoveryListStatus({loading, error, empty}: {
    loading: boolean; error: unknown; empty: boolean}) {
  if (error) return <p role="status" className="empty">{error instanceof ApiError && error.status === 404
    ? 'Recovery executions unavailable on this API version.' : errorMessage(error)}</p>;
  if (loading) return <p role="status" className="empty">Loading recovery executions…</p>;
  if (empty) return <p className="empty">No recovery executions on this page.</p>;
  return null;
}

export function RecoveryDetail({value}: {value: RecoveryExecutionDetail}) {
  return <>
    <div className="detail-heading"><h3>{value.label || `Recovery ${value.pk}`}</h3></div>
    <p>{value.caller ? 'Called recovery' : 'standalone recovery'}</p>
    <p>Scientific acceptance: not measured here</p>
    <dl>
      <dt>Recovery UUID</dt><dd><code>{value.uuid}</code></dd>
      <dt>AiiDA process state</dt><dd>{value.process_state ?? 'unknown'}</dd>
      <dt>Caller UUID (CALL_WORK)</dt><dd>{value.caller?.uuid ?? 'none (standalone)'}</dd>
      <dt>Caller process type</dt><dd>{value.caller?.process_type ?? 'none'}</dd>
      <dt>Plan UUID</dt><dd><code>{value.plan.uuid}</code></dd>
      <dt>Stored Plan fingerprint</dt><dd><code>{value.plan.fingerprint}</code></dd>
      <dt>Execution ID</dt><dd>{value.execution_id ?? 'unknown'}</dd>
      <dt>Root attempt input</dt><dd>{value.attempt ?? 'unknown'}</dd>
      <dt>Input recovery bundle UUID</dt><dd>{value.recovery.input_bundle_uuid ?? 'unknown'}</dd>
      <dt>Input prior archive UUID</dt><dd>{value.recovery.input_prior_archive_uuid ?? 'unknown'}</dd>
    </dl>
    <p className="muted">Recovery input UUIDs record association only. They do not establish a previous run or verified scientific result.</p>
    <h4>Direct CalcJob children (CALL_CALC)</h4>
    <p className="muted">Scheduler state is stored AiiDA metadata; live Slurm state is not queried here.</p>
    {!value.children.length && <p>No direct execution children recorded.</p>}
    {value.children.map(child => <article key={child.uuid}>
      <h4>CalcJob {child.pk} · {child.call_label}</h4>
      <dl>
        <dt>UUID</dt><dd><code>{child.uuid}</code></dd>
        <dt>AiiDA process state</dt><dd>{child.process_state ?? 'unknown'}</dd>
        <dt>Attempt input</dt><dd>{child.attempt ?? 'unknown'}</dd>
        <dt>Scheduler job ID</dt><dd>{child.job_id ?? 'unknown'}</dd>
        <dt>Scheduler state (stored AiiDA)</dt><dd>{child.scheduler_state ?? 'unknown'}</dd>
        <dt>Retrieved UUID</dt><dd>{child.retrieved_uuid ?? 'unknown'}</dd>
        <dt>Result UUID</dt><dd>{child.result_uuid ?? 'unknown'}</dd>
      </dl>
    </article>)}
  </>;
}

export function RecoveryExecutions({token}: {token: string}) {
  const requests = useRef(new RecoveryRequests());
  const [offset, setOffset] = useState(0);
  const [rows, setRows] = useState<RecoveryExecution[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<RecoveryExecutionDetail | null>(null);
  const [listError, setListError] = useState<unknown>(null);
  const [detailError, setDetailError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    const guard = requests.current;
    setLoading(true); setListError(null);
    void guard.list(token, offset, value => {setRows(value); setLoading(false);},
      error => {setListError(error); setRows([]); setLoading(false);});
    if (selected) void guard.detail(token, selected, value => {
      setDetail(value); setDetailError(null);
    }, error => {setDetail(null); setDetailError(error);});
    return () => guard.invalidate();
  }, [token, offset, selected, refresh]);
  useEffect(() => {
    const interval = setInterval(() => {
      if (!document.hidden) setRefresh(value => value + 1);
    }, 10000);
    return () => clearInterval(interval);
  }, []);
  function page(next: number) {
    requests.current.invalidate(); setRows([]); setDetail(null); setSelected(null);
    setDetailError(null); setListError(null); setLoading(true); setOffset(next);
  }
  function select(uuid: string) {
    if (uuid === selected) return;
    requests.current.invalidate(); setDetail(null); setDetailError(null); setSelected(uuid);
  }
  return <div className="workspace" aria-label="Recovery executions">
    <section className="panel run-list" aria-label="Recovery execution list">
      <div className="list-header"><h3>Recovery executions</h3><span className="muted">
        {rows.length ? `${offset + 1}–${offset + rows.length}` : '0 rows'}</span></div>
      <p className="muted">Read-only execution provenance, separate from Plan workflows.</p>
      <RecoveryListStatus loading={loading} error={listError} empty={!rows.length} />
      {rows.map(row => <button key={row.uuid} className={`run ${row.uuid === selected ? 'selected' : ''}`}
        onClick={() => select(row.uuid)}>
        <strong>{row.label || `Recovery ${row.pk}`}</strong>
        <span>{row.caller ? 'Called recovery' : 'standalone recovery'} · {row.process_state ?? 'unknown'}</span>
        <code>{row.uuid}</code>
      </button>)}
      <div className="pagination">
        <button disabled={loading || offset === 0} onClick={() => page(Math.max(0, offset - 25))}>Previous</button>
        <button disabled={loading || rows.length < 25} onClick={() => page(offset + 25)}>Next</button>
        <button onClick={() => setRefresh(value => value + 1)}>Refresh</button>
      </div>
    </section>
    <section className="panel details" aria-label="Recovery execution details">
      {detailError ? <p role="status">{errorMessage(detailError)}</p> : detail
        ? <RecoveryDetail value={detail} /> : <p className="empty">{selected ? 'Loading recovery execution…' : 'Select a recovery execution'}</p>}
    </section>
  </div>;
}
