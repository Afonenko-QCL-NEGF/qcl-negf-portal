import {useEffect, useRef, useState} from 'react';
import {readableBytes} from './api.ts';
import {ClusterReader, formatClusterTime, safeGraphUrl, type ClusterHost, type ClusterSnapshot, type ClusterState, type Coverage} from './cluster-status-data.ts';

const unknown = 'неизвестно';
function bytes(value: number | null) {return value === null ? unknown : readableBytes(value);}
function value(v: string | number | boolean | null) {return v === null ? unknown : String(v);}
function Reasons({reasons}: {reasons: {code: string; source_id: string}[]}) {
  return reasons.length ? <ul className="cluster-reasons">{reasons.map((r,i) => <li key={i}><code>{r.source_id}</code>: {r.code}</li>)}</ul> : null;
}
function CoverageView({coverage}: {coverage: Coverage}) {
  return <div className="cluster-coverage"><span>coverage <code>{coverage.source_id}</code>: <strong>{coverage.status}</strong> · {coverage.complete ? 'полный' : 'неполный'} · {coverage.source_id.startsWith('controller-cache:') ? 'получение cache' : 'измерение'} {formatClusterTime(coverage.observed_at)}</span><Reasons reasons={coverage.reasons} /></div>;
}
function Freshness({freshness}: {freshness: {status: string; observed_at: number | null}}) {
  return <span>{freshness.status} · измерение {formatClusterTime(freshness.observed_at)}</span>;
}
function Completeness({complete}: {complete: boolean}) {return <span>{complete ? 'полный' : 'неполный'} (не определяет свежесть)</span>;}
function Host({host: h}: {host: ClusterHost}) {
  const native = h.native, resource = h.resource;
  const graphs = h.graph_links.flatMap(g => {const href = safeGraphUrl(g.href);return href ? [{label:g.label,href}] : [];});
  return <article className="cluster-host">
    <h3>Host <code>{h.host_id}</code></h3><Reasons reasons={h.reasons} />
    {native ? <>
      <p>Измерение native: {formatClusterTime(native.observed_at)} · <Completeness complete={native.complete} /></p>
      <CoverageView coverage={native.coverage} />
      <dl><dt>Host RAM total</dt><dd>{bytes(native.host_memory.total_bytes)}</dd><dt>Host RAM available</dt><dd>{bytes(native.host_memory.available_bytes)}</dd></dl>
      <h4>VM — native</h4>
      {native.vms.length ? <div className="cluster-table-scroll"><table><thead><tr><th>VM / состояние</th><th>QEMU RSS</th><th>Assigned / configured / max RAM</th><th>vCPU configured / online</th><th>Измерение / coverage</th></tr></thead><tbody>{native.vms.map(vm => <tr key={vm.vm_id}><th scope="row">{vm.vm_id} · {vm.power_state}</th><td>{bytes(vm.qemu_rss_bytes)}</td><td>{bytes(vm.assigned_bytes)} / {bytes(vm.configured_bytes)} / {bytes(vm.configured_max_bytes)}</td><td>{value(vm.configured_vcpus)} / {value(vm.online_vcpus)}</td><td>{formatClusterTime(vm.observed_at)}<CoverageView coverage={vm.coverage} /></td></tr>)}</tbody></table></div> : <p>VM: строки не предоставлены этим снимком.</p>}
      {!native.complete && <p>Native неполный. Вложенный cursor не доступен через этот экран.</p>}
      <h4>OOM — owner evidence</h4><CoverageView coverage={native.oom.coverage} />
      {native.oom.events.length ? <ul className="cluster-items">{native.oom.events.map((event,i) => <li key={i}><code>{event.event_id}</code> · {event.cause} · scope {event.scope} · VM {value(event.vm_id)} · unit {value(event.unit_id)} · confidence {event.confidence} · source <code>{event.source_id}</code> · событие {formatClusterTime(event.event_at)}<Reasons reasons={event.reasons} /></li>)}</ul> : <p>OOM: события не предоставлены этим снимком.</p>}
      {!native.oom.coverage.complete && <p>OOM coverage: неполный; продолжение здесь недоступно.</p>}
    </> : <p>native: неизвестно — источник не предоставлен.</p>}
    <h4>Services</h4>
    {h.services.length ? <ul className="cluster-items">{h.services.map(s => <li key={s.service_id}><strong>{s.service_id}</strong>: {value(s.active_state)} / {value(s.sub_state)} · result {value(s.result)} · invocation <code>{value(s.invocation_id)}</code> · измерение {formatClusterTime(s.observed_at)}<CoverageView coverage={s.coverage} /></li>)}</ul> : <p>Services: строки не предоставлены; состояние сервисов неизвестно.</p>}
    <h4>Resource grants и operations</h4>
    {resource ? <>
      <p>Resource: <strong>{resource.status}</strong> · измерение {formatClusterTime(resource.observed_at)} · <Completeness complete={resource.complete} /> · generation <code>{value(resource.state_generation)}</code></p>
      {resource.vms.length ? <div className="cluster-table-scroll"><table><thead><tr><th>VM / grant / boot</th><th>RAM charged / desired / floor</th><th>RAM assigned / configured</th><th>vCPU configured / online</th><th>Maintenance / failure / freshness</th></tr></thead><tbody>{resource.vms.map(vm => <tr key={vm.vm_id}><th scope="row">{vm.vm_id} · {vm.grant_state} · {vm.boot_phase}</th><td>{bytes(vm.charged_bytes)} / {bytes(vm.desired_bytes)} / {bytes(vm.protected_floor_bytes)}</td><td>{bytes(vm.current_assigned_bytes)} / {bytes(vm.current_configured_bytes)}</td><td>{value(vm.cpu.configured_vcpus)} / {value(vm.cpu.online_vcpus)}</td><td>{value(vm.maintenance_required)} · failure {value(vm.failure_code)} · <Freshness freshness={vm.native_freshness} /></td></tr>)}</tbody></table></div> : <p>Grants: строки не предоставлены.</p>}
      {resource.operations.length ? <ul className="cluster-items">{resource.operations.map((op,i) => <li key={i}><strong>{op.phase}</strong> · admission {op.admission} · request <code>{value(op.request_id)}</code> · VM {value(op.vm_id)} · workload {op.workload ? `${op.workload.kind}:${op.workload.native_id}` : unknown}<br />failure <strong>{value(op.failure_code)}</strong> · cleanup {op.cleanup} · requested {bytes(op.requested_workload_bytes)} / target {bytes(op.target_bytes)} / charged {bytes(op.charged_bytes)}<br /><Freshness freshness={op.native_freshness} /></li>)}</ul> : <p>Operations: строки не предоставлены.</p>}
      {!resource.complete && <p>Resource неполный; продолжение только через root cursor при доступной кнопке.</p>}
    </> : <p>resource: неизвестно — источник не предоставлен.</p>}
    <h4>Telemetry storage</h4>
    {h.storage ? <><p>{h.storage.status} · used {bytes(h.storage.used_bytes)} · available {bytes(h.storage.available_bytes)} · измерение {formatClusterTime(h.storage.observed_at)}</p><Reasons reasons={h.storage.reasons} /></> : <p>storage: неизвестно — источник не предоставлен.</p>}
    <h4>Графики Proxmox</h4>
    {graphs.length ? <ul>{graphs.map((g,i) => <li key={i}><a href={g.href} target="_blank" rel="noopener noreferrer">{g.label} ↗</a></li>)}</ul> : <p>Графики не предоставлены.</p>}
    {graphs.length < h.graph_links.length && <p>Небезопасные ссылки скрыты.</p>}
  </article>;
}
function displayStatus(snapshot: ClusterSnapshot): string {
  const coverages = [...snapshot.coverage.sources,...snapshot.hosts.flatMap(h => [...(h.native ? [h.native.coverage,h.native.oom.coverage,...h.native.vms.map(vm => vm.coverage)] : []),...h.services.map(s => s.coverage)]),...(snapshot.current_context ? [snapshot.current_context.coverage,...snapshot.current_context.jobs.map(j => j.coverage)] : [])];
  if (snapshot.status === 'unknown' || !coverages.length) return 'unknown';
  const resourceFreshness = snapshot.hosts.flatMap(h => [...(h.resource?.vms.map(vm => vm.native_freshness) ?? []),...(h.resource?.operations.map(op => op.native_freshness) ?? [])]);
  const statuses = new Set([...coverages,...resourceFreshness].map(c => c.status));
  if (statuses.has('stale')) return 'stale / partial';
  const missing = snapshot.hosts.some(h => !h.native || !h.resource || !h.native.complete || !h.native.oom.coverage.complete || !h.resource.complete || h.reasons.length || h.resource.status !== 'ok' || !h.storage || h.storage.status !== 'qualified' || h.storage.observed_at === null
    || h.native.observed_at === null || Object.values(h.native.host_memory).some(v => v === null)
    || h.native.vms.some(vm => [vm.observed_at,vm.qemu_rss_bytes,vm.assigned_bytes,vm.configured_bytes,vm.configured_max_bytes,vm.configured_vcpus,vm.online_vcpus].some(v => v === null)));
  if (snapshot.status !== 'ok' || !snapshot.complete || snapshot.truncated || missing || coverages.some(c => c.status !== 'fresh' || !c.complete || c.observed_at === null) || resourceFreshness.some(c => c.status !== 'fresh' || c.observed_at === null) || !snapshot.current_context || snapshot.current_context.status !== 'fresh' || !snapshot.current_context.complete || snapshot.current_context.observed_at === null) return 'partial';
  return 'actual (coverage fresh)';
}
function Snapshot({snapshot: p, historical}: {snapshot: ClusterSnapshot; historical: boolean}) {
  const c = p.current_context;
  return <>
    <p className="cluster-status-label">{historical ? 'Историческая coverage последнего снимка' : 'Состояние'}: <strong>{displayStatus(p)}</strong> · owner status последней страницы {p.status} · <Completeness complete={p.complete} /></p>
    <p>Чтение projection: {formatClusterTime(p.read_at)} · первая страница: {formatClusterTime(p.first_read_at)} · страниц {p.pages} · registry <code>{p.registry_generation}</code></p>
    {p.truncated && <p role="status" className="cluster-warning">Локальное усечение: достигнут предел памяти/страниц; снимок неполный, продолжение остановлено.</p>}
    <Reasons reasons={p.coverage.reasons} />
    <h3>Источники coverage</h3>
    {p.coverage.sources.length ? p.coverage.sources.map((s,i) => <div key={i}>{s.source_id.startsWith('controller-cache:') && <p>Получение cache source: {formatClusterTime(s.observed_at)} · <code>{s.source_id}</code> (без привязки к host)</p>}<CoverageView coverage={s} /></div>) : <p>coverage: неизвестно — источники не предоставлены.</p>}
    {p.hosts.map(h => <Host key={h.host_id} host={h} />)}
    {!p.hosts.length && <p>Hosts: строки не предоставлены этим снимком.</p>}
    <h3>Current jobs / waits</h3>
    <p>Ограниченный текущий снимок; полнота истории не установлена.</p>
    {c ? <>
      <p>current_context: <strong>{c.status}</strong> · <Completeness complete={c.complete} /> · чтение context {formatClusterTime(c.read_at)} · измерение context {formatClusterTime(c.observed_at)}</p><CoverageView coverage={c.coverage} />
      {c.jobs.length ? <ul className="cluster-items">{c.jobs.map(j => <li key={j.job_id}><strong>Job {j.job_id}</strong>: {j.state} / {j.native_state} · reason {j.reason || 'не предоставлена'} · nodes {j.node_expression || unknown}<br />submit {formatClusterTime(j.submit_time)} · start {formatClusterTime(j.start_time)} · restarts {value(j.restarts)} · CPU {value(j.cpus)} · requested RAM / node {bytes(j.requested_memory.per_node_bytes)} / CPU {bytes(j.requested_memory.per_cpu_bytes)}<CoverageView coverage={j.coverage} /></li>)}</ul> : <p>Jobs: строки не предоставлены.</p>}
      {!c.complete && <p>Current context неполный; вложенное продолжение здесь недоступно.</p>}
    </> : <p>current_context: неизвестно — источник не предоставлен.</p>}
    {!p.complete && !p.next_cursor && <p>Продолжение недоступно: root cursor отсутствует, снимок неполный.</p>}
  </>;
}
export function ClusterStatusView({state, onRefresh, onMore}: {state: ClusterState; onRefresh: () => void; onMore: () => void}) {
  return <div className="cluster-content" aria-busy={state.busy}>
    <p>Cache projection · обновление не чаще чем через 30 секунд после ответа, пока экран открыт и видим.</p>
    <button type="button" disabled={state.busy} onClick={onRefresh}>Обновить первую страницу</button>
    {state.busy && <p role="status">Загрузка…</p>}
    {state.error && <p role="status">Текущее состояние: <strong>unavailable / unknown</strong></p>}
    {state.error && <div role="alert" className="cluster-error"><p>{state.error}</p>{state.snapshot && <p>Последний успешный снимок: {formatClusterTime(state.snapshot.read_at)}. Текущая доступность неизвестна.</p>}</div>}
    {state.drift && <p role="status">Snapshot drift: все страницы отброшены; требуется явное обновление.</p>}
    {state.snapshot ? <Snapshot snapshot={state.snapshot} historical={Boolean(state.error)} /> : !state.busy && <p>Снимок не получен; значения неизвестны.</p>}
    {state.snapshot?.next_cursor && !state.snapshot.truncated && !state.drift && <button type="button" disabled={state.busy} onClick={onMore}>Загрузить ещё</button>}
  </div>;
}
export function ClusterStatus({token}: {token: string}) {
  const [expanded,setExpanded] = useState(false);
  const [state,setState] = useState<ClusterState>({snapshot:null,busy:false,error:null,drift:false});
  const reader = useRef<ClusterReader | null>(null), open = useRef(expanded);open.current = expanded;
  useEffect(() => {
    const controller = new ClusterReader(token,setState);reader.current = controller;
    const sync = () => controller.activate(Boolean(token) && open.current && document.visibilityState === 'visible');
    document.addEventListener('visibilitychange',sync);sync();
    return () => {document.removeEventListener('visibilitychange',sync);controller.dispose();if (reader.current === controller) reader.current = null;};
  },[token]);
  useEffect(() => {reader.current?.activate(Boolean(token) && expanded && document.visibilityState === 'visible');},[expanded,token]);
  return <section className="panel cluster-panel"><details onToggle={event => setExpanded(event.currentTarget.open)}><summary>Состояние кластера</summary>{expanded && <ClusterStatusView state={state} onRefresh={() => {void reader.current?.refresh();}} onMore={() => {void reader.current?.loadMore();}} />}</details></section>;
}
