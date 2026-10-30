import {api, ApiError, errorMessage} from './api.ts';

type Reason = {code: string; source_id: string};
export type Coverage = {source_id: string; status: string; observed_at: number | null; host_boot_id: string | null; generation: string | null; complete: boolean; reasons: Reason[]};
type Freshness = {status: string; observed_at: number | null};
type NativeVM = {vm_id: string; power_state: string; qemu_rss_bytes: number | null; assigned_bytes: number | null; configured_bytes: number | null; configured_max_bytes: number | null; configured_vcpus: number | null; online_vcpus: number | null; observed_at: number | null; coverage: Coverage};
type OomEvent = {event_id: string; cause: string; scope: string; vm_id: string | null; event_at: number | null; source_id: string; confidence: string; unit_id: string | null; reasons: Reason[]};
type Native = {schema: string; observed_at: number | null; host_id: string; sample_generation: string | null; host_memory: {total_bytes: number | null; available_bytes: number | null}; vms: NativeVM[]; coverage: Coverage; complete: boolean; next_cursor: string | null; oom: {events: OomEvent[]; coverage: Coverage; next_cursor: string | null}};
type Service = {service_id: string; active_state: string | null; sub_state: string | null; result: string | null; invocation_id: string | null; observed_at: number | null; coverage: Coverage};
type Grant = {vm_id: string; grant_state: string; charged_bytes: number | null; desired_bytes: number | null; current_assigned_bytes: number | null; current_configured_bytes: number | null; protected_floor_bytes: number | null; boot_phase: string; maintenance_required: boolean | null; failure_code: string | null; native_freshness: Freshness; cpu: {configured_vcpus: number | null; online_vcpus: number | null}};
type Operation = {schema: string; request_id: string | null; workload: {kind: string; native_id: string} | null; vm_id: string | null; phase: string; admission: string; failure_code: string | null; requested_workload_bytes: number | null; target_bytes: number | null; charged_bytes: number | null; cleanup: string; native_freshness: Freshness};
type Resource = {schema: string; observed_at: number; state_generation: string | null; status: string; vms: Grant[]; operations: Operation[]; complete: boolean; next_cursor: string | null};
type Storage = {schema: string; status: string; observed_at: number | null; used_bytes: number | null; available_bytes: number | null; reasons: Reason[]};
export type ClusterHost = {host_id: string; native: Native | null; resource: Resource | null; services: Service[]; storage: Storage | null; graph_links: {label: string; href: string}[]; reasons: Reason[]};
type Job = {job_id: string; state: string; native_state: string; reason: string; node_expression: string; submit_time: number | null; start_time: number | null; restarts: number | null; cpus: number | null; requested_memory: {per_node_bytes: number | null; per_cpu_bytes: number | null}; generation: string | null; coverage: Coverage};
type Context = {schema: string; read_at: number; observed_at: number | null; cluster_id: string | null; controller_boot_id: string | null; sample_generation: string | null; status: string; jobs: Job[]; coverage: Coverage; complete: boolean; next_cursor: string | null};
export type ClusterPage = {schema: string; read_at: number; status: string; registry_generation: string; hosts: ClusterHost[]; complete: boolean; next_cursor: string | null; coverage: {sources: Coverage[]; reasons: Reason[]}; current_context: Context | null};
export type ClusterSnapshot = ClusterPage & {pages: number; truncated: boolean; first_read_at: number};
export const MAX_CLUSTER_PAGES = 8;
export const MAX_CLUSTER_ROWS = 2048;

// Validate the displayed contract at the browser boundary. Platform still owns the
// full nested schema; these readers keep missing fields distinct from explicit null.
type Reader<T> = (value: unknown) => T;
function invalid(): never {throw new Error('Invalid cluster projection field/schema');}
const text: Reader<string> = v => typeof v === 'string' && v.length <= 8192 ? v : invalid();
const integer: Reader<number> = v => typeof v === 'number' && Number.isSafeInteger(v) && v >= 0 ? v : invalid();
const boolean: Reader<boolean> = v => typeof v === 'boolean' ? v : invalid();
const nullable = <T>(read: Reader<T>): Reader<T | null> => v => v === null ? null : read(v);
const choice = (...allowed: string[]): Reader<string> => v => typeof v === 'string' && allowed.includes(v) ? v : invalid();
const array = <T>(read: Reader<T>, max = 128): Reader<T[]> => v => Array.isArray(v) && v.length <= max ? v.map(read) : invalid();
function object<T>(shape: {[K in keyof T]: Reader<T[K]>}): Reader<T> {
  return value => {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return invalid();
    const source = value as Record<string, unknown>, result: Record<string, unknown> = {...source};
    for (const [key, read] of Object.entries(shape) as [string, Reader<unknown>][]) result[key] = read(source[key]);
    return result as T;
  };
}
const numberOrNull = nullable(integer), textOrNull = nullable(text);
const reason = object<Reason>({code: text, source_id: text});
const coverage = object<Coverage>({source_id: text, status: choice('fresh','stale','not_measured','inconsistent','unsupported'), observed_at: numberOrNull, host_boot_id: textOrNull, generation: textOrNull, complete: boolean, reasons: array(reason)});
const freshness = object<Freshness>({status: choice('fresh','stale','not_measured','inconsistent'), observed_at: numberOrNull});
const nativeVM = object<NativeVM>({vm_id:text,power_state:choice('running','stopped','missing','unknown'),qemu_rss_bytes:numberOrNull,assigned_bytes:numberOrNull,configured_bytes:numberOrNull,configured_max_bytes:numberOrNull,configured_vcpus:numberOrNull,online_vcpus:numberOrNull,observed_at:numberOrNull,coverage});
const oomEvent = object<OomEvent>({event_id:text,cause:choice('host_kernel_qemu_oom','guest_cgroup_oom','systemd_oomd','unknown_vm_loss','unknown','native_unit_kernel_oom','native_unit_oomd'),scope:choice('host','guest','unknown'),vm_id:textOrNull,event_at:numberOrNull,source_id:text,confidence:choice('observed','indication','unknown'),unit_id:textOrNull,reasons:array(reason)});
const native = object<Native>({schema:choice('qcl.native-status.v2'),observed_at:numberOrNull,host_id:text,sample_generation:textOrNull,host_memory:object({total_bytes:numberOrNull,available_bytes:numberOrNull}),vms:array(nativeVM),coverage,complete:boolean,next_cursor:textOrNull,oom:object({events:array(oomEvent,1024),coverage,next_cursor:textOrNull})});
const service = object<Service>({service_id:text,active_state:textOrNull,sub_state:textOrNull,result:textOrNull,invocation_id:textOrNull,observed_at:numberOrNull,coverage});
const grant = object<Grant>({vm_id:text,grant_state:choice('idle','active','ambiguous','unknown'),charged_bytes:numberOrNull,desired_bytes:numberOrNull,current_assigned_bytes:numberOrNull,current_configured_bytes:numberOrNull,protected_floor_bytes:numberOrNull,boot_phase:choice('cold_reserved','boot_unknown','running_quarantined','binding_ready','unknown'),maintenance_required:nullable(boolean),failure_code:textOrNull,native_freshness:freshness,cpu:object({configured_vcpus:numberOrNull,online_vcpus:numberOrNull})});
const operation = object<Operation>({schema:choice('qcl.host-resource-operation.v1'),request_id:textOrNull,workload:nullable(object({kind:choice('slurm_allocation','systemd_build'),native_id:text})),vm_id:textOrNull,phase:choice('HOST_GRANT','GROW_UNKNOWN','GUEST_ADMIT','RUN','SHRINK_READY','SHRINK_DEFERRED','SHRINK_UNKNOWN','RELEASE','RESOURCE_WAIT','NOT_INITIALIZED','UNSUPPORTED','REJECTED'),admission:choice('ready','resource_wait','reconciliation_required','unsupported','rejected'),failure_code:textOrNull,requested_workload_bytes:numberOrNull,target_bytes:numberOrNull,charged_bytes:numberOrNull,cleanup:choice('not_started','pending','confirmed','unknown'),native_freshness:freshness});
const resource = object<Resource>({schema:choice('qcl.host-resource-projection.v1'),observed_at:integer,state_generation:textOrNull,status:choice('ok','not_initialized','upgrade_required','reconciliation_required'),vms:array(grant),operations:array(operation),complete:boolean,next_cursor:textOrNull});
const storage = object<Storage>({schema:choice('qcl.telemetry-storage.v2'),status:choice('qualified','degraded','unknown','unsupported'),observed_at:numberOrNull,used_bytes:numberOrNull,available_bytes:numberOrNull,reasons:array(reason)});
const host = object<ClusterHost>({host_id:text,native:nullable(native),resource:nullable(resource),services:array(service),storage:nullable(storage),graph_links:array(object({label:text,href:text}),16),reasons:array(reason)});
const job = object<Job>({job_id:text,state:choice('queued','running','finishing','retained_terminal','unknown'),native_state:text,reason:text,node_expression:text,submit_time:numberOrNull,start_time:numberOrNull,restarts:numberOrNull,cpus:numberOrNull,requested_memory:object({per_node_bytes:numberOrNull,per_cpu_bytes:numberOrNull}),generation:textOrNull,coverage});
const context = object<Context>({schema:choice('qcl.current-context.v1'),read_at:integer,observed_at:numberOrNull,cluster_id:textOrNull,controller_boot_id:textOrNull,sample_generation:textOrNull,status:choice('fresh','stale','partial','unknown','disabled','unsupported'),jobs:array(job),coverage,complete:boolean,next_cursor:textOrNull});
const projection = object<ClusterPage>({schema:choice('qcl.cluster-projection.v2'),read_at:integer,status:choice('ok','partial','unknown'),registry_generation:text,hosts:array(host),complete:boolean,next_cursor:textOrNull,coverage:object({sources:array(coverage),reasons:array(reason)}),current_context:nullable(context)});
export function parseCluster(value: unknown): ClusterPage {
  let nodes = 0, characters = 0;
  function bounded(v: unknown, depth = 0) {
    if (++nodes > 60000 || depth > 20) return invalid();
    if (typeof v === 'string') {characters += v.length; if (characters > 2_000_000) return invalid();}
    else if (typeof v === 'number') integer(v);
    else if (Array.isArray(v)) {if (v.length > 1024) return invalid(); v.forEach(x => bounded(x,depth+1));}
    else if (v && typeof v === 'object') Object.values(v).forEach(x => bounded(x,depth+1));
  }
  bounded(value);
  const page = projection(value);
  const ids = new Set<string>();
  for (const h of page.hosts) {
    if (ids.has(h.host_id) || h.native && h.native.host_id !== h.host_id) return invalid();
    ids.add(h.host_id);
  }
  return page;
}
export function safeGraphUrl(value: string): string | null {
  if (/[\s\u0000-\u001f\u007f]/.test(value)) return null;
  try {const u = new URL(value);return u.protocol === 'https:' && !u.username && !u.password ? u.href : null;} catch {return null;}
}
export function formatClusterTime(value: number | null): string {
  if (value === null) return 'неизвестно';
  const date = new Date(value * 1000);
  return Number.isFinite(date.getTime()) ? `${date.toISOString()} UTC` : `${value} Unix seconds (вне диапазона даты)`;
}
export class SnapshotDrift extends Error {constructor() {super('Snapshot drift: обновите первую страницу');}}
function identical(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (Array.isArray(a) && Array.isArray(b)) return a.length === b.length && a.every((v,i) => identical(v,b[i]));
  if (!record(a) || !record(b)) return false;
  const keys = Object.keys(a).sort();return identical(keys,Object.keys(b).sort()) && keys.every(k => identical(a[k],b[k]));
}
// Compare two views of one immutable cache, not a monotone transition between
// views. Public normalizers start from that cache on every read. A bounded append
// can hide only its final original reason; retain the exact known prefix.
type ReadTimes = readonly [number,number];
type Measurements = readonly [unknown,unknown];
type ReasonConstraint = {prefix: unknown[]; length: number; absent?: unknown};
type EvidenceOption = {states: string[]; reasons: ReasonConstraint};
function record(value: unknown): value is Record<string, unknown> {return Boolean(value) && typeof value === 'object' && !Array.isArray(value);}
function equalExcept(a: Record<string, unknown>, b: Record<string, unknown>, ignored: string[], reads: ReadTimes, inherited?: Measurements): boolean {
  const keys = Object.keys(a).filter(k => !ignored.includes(k)).sort();
  const at: Measurements | undefined = 'observed_at' in a ? [a.observed_at,b.observed_at] : inherited;
  return identical(keys,Object.keys(b).filter(k => !ignored.includes(k)).sort()) && keys.every(k => compatibleObservation(a[k],b[k],reads,['oom','capabilities'].includes(k) ? undefined : at));
}
function exactReasons(reasons: unknown[]): ReasonConstraint {return {prefix:reasons,length:reasons.length};}
function appendedReasons(output: unknown[], added: Reason): ReasonConstraint[] {
  const result: ReasonConstraint[] = [];
  // Owner adds nothing when this exact reason is already in the base list.
  if (output.some(r => identical(r,added))) result.push(exactReasons(output));
  if (output.length && identical(output.at(-1),added)) {
    const prefix = output.slice(0,-1);
    if (!prefix.some(r => identical(r,added))) {
      result.push({prefix,length:prefix.length,absent:added});
      if (output.length === 128) result.push({prefix,length:128,absent:added});
    }
  }
  return result;
}
function compatibleReasons(a: ReasonConstraint, b: ReasonConstraint): boolean {
  if (a.length !== b.length) return false;
  const shared = Math.min(a.prefix.length,b.prefix.length);
  if (!identical(a.prefix.slice(0,shared),b.prefix.slice(0,shared))) return false;
  // Any unknown final entry must satisfy both absence constraints. No concrete
  // replacement reason is invented or stored in the resulting snapshot.
  return !(a.absent && b.prefix.some(r => identical(r,a.absent))) && !(b.absent && a.prefix.some(r => identical(r,b.absent)));
}
function compatibleEvidence(a: EvidenceOption[], b: EvidenceOption[]) {
  return a.some(x => b.some(y => x.states.some(state => y.states.includes(state)) && compatibleReasons(x.reasons,y.reasons)));
}
function states(statuses: string[]) {return statuses.flatMap(status => [`${status}:true`,`${status}:false`]);}
const coverageStates = states(['fresh','stale','not_measured','inconsistent','unsupported']);
function coverageOrigins(c: Record<string,unknown>, now: number, measurement: unknown): EvidenceOption[] {
  const reasons = c.reasons as unknown[], times = [measurement,c.observed_at];
  const future = times.some(t => typeof t === 'number' && t > now), missing = times.some(t => t === null);
  const result: EvidenceOption[] = [];
  const action = (code: string, status: string, baseStates: string[]) => {
    if (c.status === status && c.complete === false) for (const r of appendedReasons(reasons,{code,source_id:String(c.source_id)})) result.push({states:baseStates,reasons:r});
  };
  if (future) action('clock_future','inconsistent',coverageStates);
  else if (missing) {
    if (!['fresh','stale'].includes(String(c.status))) result.push({states:[`${c.status}:${c.complete}`],reasons:exactReasons(reasons)});
    action('sample_not_measured','not_measured',states(['fresh','stale']));
  } else {
    // fresh_seconds is owner configuration, absent from this DTO. A past sample
    // may be unchanged or stale, but only the exact public append is admitted.
    result.push({states:[`${c.status}:${c.complete}`],reasons:exactReasons(reasons)});
    action('sample_stale','stale',states(['fresh','stale']));
  }
  return result;
}
function storageOrigins(s: Record<string,unknown>, now: number): EvidenceOption[] {
  const reasons = s.reasons as unknown[], at = s.observed_at, result: EvidenceOption[] = [];
  const future = typeof at === 'number' && at > now;
  if (typeof at === 'number' && !future) result.push({states:[`${s.status}:${s.persist_permitted}`],reasons:exactReasons(reasons)});
  if (s.status === 'unknown' && s.persist_permitted === false) {
    const code = future ? 'clock_future' : 'storage_stale';
    for (const r of appendedReasons(reasons,{code,source_id:'telemetry'})) result.push({states:states(['qualified','degraded','unknown','unsupported']),reasons:r});
  }
  return result;
}
function contextOrigins(c: Record<string,unknown>, now: number): string[] {
  const at = c.observed_at;
  const all = states(['fresh','stale','partial','unknown','disabled','unsupported']);
  if (typeof at === 'number' && at > now) return c.status === 'unknown' && c.complete === false ? all : [];
  if (typeof at === 'number' && c.status === 'stale' && c.complete === false) return all;
  return [`${c.status}:${c.complete}`];
}
function compatibleObservation(a: unknown, b: unknown, reads: ReadTimes, inherited?: Measurements): boolean {
  if (identical(a,b)) return true;
  if (Array.isArray(a) && Array.isArray(b)) return a.length === b.length && a.every((v,i) => compatibleObservation(v,b[i],reads,inherited));
  if (!record(a) || !record(b)) return false;
  if ('source_id' in a && 'host_boot_id' in a && 'complete' in a && 'reasons' in a) {
    if (!equalExcept(a,b,['status','complete','reasons'],reads,inherited)) return false;
    return compatibleEvidence(coverageOrigins(a,reads[0],inherited ? inherited[0] : a.observed_at),coverageOrigins(b,reads[1],inherited ? inherited[1] : b.observed_at));
  }
  if (a.schema === 'qcl.telemetry-storage.v2' && b.schema === a.schema) return equalExcept(a,b,['status','persist_permitted','reasons'],reads) && compatibleEvidence(storageOrigins(a,reads[0]),storageOrigins(b,reads[1]));
  if (a.schema === 'qcl.current-context.v1' && b.schema === a.schema) return equalExcept(a,b,['status','complete'],reads) && contextOrigins(a,reads[0]).some(state => contextOrigins(b,reads[1]).includes(state));
  return equalExcept(a,b,[],reads,inherited);
}
function stableHost(h: ClusterHost) {const {resource: _resource,...stable} = h;return stable;}
function mergeRows<T>(a: T[], b: T[], key: (row: T) => string): T[] {
  const rows = new Map(a.map(row => [key(row),row]));
  for (const row of b) {const old = rows.get(key(row));if (old && !identical(old,row)) throw new SnapshotDrift();rows.set(key(row),row);}
  return [...rows.values()];
}
function countRows(p: ClusterPage) {return (p.current_context?.jobs.length ?? 0) + p.hosts.reduce((n,h) => n+1+h.services.length+(h.native?.vms.length ?? 0)+(h.native?.oom.events.length ?? 0)+(h.resource?.vms.length ?? 0)+(h.resource?.operations.length ?? 0),0);}
function contextIdentity(c: Context | null) {if (!c) return null;const {read_at: _read, ...identity} = c;return identity;}
export function mergeCluster(previous: ClusterSnapshot | null, page: ClusterPage): ClusterSnapshot {
  if (!previous) {
    // Refuse oversized first pages rather than retaining an unbounded row tree.
    if (countRows(page) > MAX_CLUSTER_ROWS) return {...page,hosts:[],current_context:null,pages:1,first_read_at:page.read_at,truncated:true,complete:false,next_cursor:null};
    return {...page,pages:1,first_read_at:page.read_at,truncated:false};
  }
  const reads: ReadTimes = [previous.read_at,page.read_at];
  if (previous.registry_generation !== page.registry_generation || !compatibleObservation(contextIdentity(previous.current_context),contextIdentity(page.current_context),reads)) throw new SnapshotDrift();
  if (previous.pages >= MAX_CLUSTER_PAGES) return {...previous,truncated:true,complete:false,next_cursor:null};
  const hosts = [...previous.hosts];
  for (const h of page.hosts) {
    const index = hosts.findIndex(old => old.host_id === h.host_id);
    if (index < 0) {hosts.push(h);continue;}
    const old = hosts[index];
    if (!old.resource || !h.resource || old.resource.state_generation === null || old.resource.state_generation !== h.resource.state_generation || old.native?.sample_generation !== h.native?.sample_generation) throw new SnapshotDrift();
    if (!compatibleObservation(stableHost(old),stableHost(h),reads) || !equalExcept(old.resource as unknown as Record<string,unknown>,h.resource as unknown as Record<string,unknown>,['observed_at','vms','operations','complete','next_cursor'],reads)) throw new SnapshotDrift();
    hosts[index] = {...h,resource:{...h.resource,vms:mergeRows(old.resource.vms,h.resource.vms,x => x.vm_id),operations:mergeRows(old.resource.operations,h.resource.operations,x => JSON.stringify([x.request_id,x.workload,x.vm_id]))}};
  }
  const sources = new Map(previous.coverage.sources.map(c => [c.source_id,c]));
  for (const c of page.coverage.sources) {const old = sources.get(c.source_id);if (old && !compatibleObservation(old,c,reads)) throw new SnapshotDrift();sources.set(c.source_id,c);}
  const result: ClusterSnapshot = {...page,hosts,coverage:{sources:[...sources.values()],reasons:mergeRows(previous.coverage.reasons,page.coverage.reasons,x => JSON.stringify(x))},pages:previous.pages+1,first_read_at:previous.first_read_at,truncated:false};
  if (countRows(result) > MAX_CLUSTER_ROWS || sources.size > MAX_CLUSTER_ROWS) return {...previous,truncated:true,complete:false,next_cursor:null};
  return result;
}
export type ClusterState = {snapshot: ClusterSnapshot | null; busy: boolean; error: string | null; drift: boolean};
type Clock = {set: (fn: () => void, ms: number) => unknown; clear: (id: unknown) => void};
type FetchCluster = (token: string, path: string, signal?: AbortSignal) => Promise<unknown>;
const realClock: Clock = {set:(fn,ms) => setTimeout(fn,ms),clear:id => clearTimeout(id as ReturnType<typeof setTimeout>)};
export class ClusterReader {
  state: ClusterState = {snapshot:null,busy:false,error:null,drift:false};
  private active = false;
  private disposed = false;
  private generation = 0;
  private timer: unknown = null;
  private pending: AbortController | null = null;
  private resume = false;
  private token: string;
  private changed: (state: ClusterState) => void;
  private read: FetchCluster;
  private clock: Clock;
  constructor(token: string, changed: (state: ClusterState) => void,
    read: FetchCluster = (token,path,signal) => api<unknown>(token,path,undefined,signal), clock: Clock = realClock) {
    this.token = token;this.changed = changed;this.read = read;this.clock = clock;
  }
  private publish(patch: Partial<ClusterState>) {this.state = {...this.state,...patch};if (!this.disposed) this.changed(this.state);}
  private clearTimer() {if (this.timer !== null) this.clock.clear(this.timer);this.timer = null;}
  activate(value: boolean) {
    if (this.disposed || this.active === value) return;
    this.active = value;this.generation++;this.clearTimer();
    if (!value) {this.pending?.abort();this.resume = false;return;}
    if (this.state.drift) return;
    if (this.pending) this.resume = true;
    else void this.refresh();
  }
  dispose() {this.activate(false);this.disposed = true;this.token = '';this.generation++;}
  refresh() {return this.request(false);}
  loadMore() {return this.request(true);}
  private async request(append: boolean): Promise<boolean> {
    if (!this.active || this.disposed || this.pending || append && (!this.state.snapshot?.next_cursor || this.state.snapshot.truncated || this.state.drift)) return false;
    this.clearTimer();
    const cursor = append ? this.state.snapshot!.next_cursor : null;
    const generation = ++this.generation;
    const controller = new AbortController();this.pending = controller;
    this.publish({busy:true,...(!append ? {drift:false} : {})});
    try {
      const query = new URLSearchParams({limit:'64'});if (cursor) query.set('cursor',cursor);
      const raw = await this.read(this.token,`/cluster/status?${query}`,controller.signal);
      if (generation !== this.generation || !this.active || this.disposed) return false;
      const page = parseCluster(raw);
      this.publish({snapshot:mergeCluster(append ? this.state.snapshot : null,page),error:null});
      return true;
    } catch (error) {
      if (generation !== this.generation || !this.active || this.disposed) return false;
      if (error instanceof SnapshotDrift || error instanceof ApiError && error.status === 409 && error.code === 'stale_cursor') this.publish({snapshot:null,drift:true,error:'Snapshot drift: страницы отброшены. Обновите первую страницу.'});
      else this.publish({error:error instanceof ApiError ? `HTTP ${error.status}: ${error.message}` : `unavailable: ${errorMessage(error)}`});
      return false;
    } finally {
      this.pending = null;
      if (!this.disposed) {
        this.publish({busy:false});
        if (this.active && this.resume) {this.resume = false;void this.refresh();}
        else if (this.active && !this.state.drift) this.timer = this.clock.set(() => {this.timer = null;void this.refresh();},30_000);
      }
    }
  }
}
