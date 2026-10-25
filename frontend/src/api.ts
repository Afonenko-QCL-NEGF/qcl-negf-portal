import type { PlanStructure } from "./run-structure-data.ts";
export interface Run {
  uuid: string;
  pk: number;
  label: string;
  process_state: string;
  exit_status: number | null;
  is_finished_ok: boolean;
  ctime: string;
  mtime: string;
  results?: Record<string, unknown>;
  children?: ChildRun[];
  plan_fingerprint?: string;
  plan_structure?: PlanStructure;
  plan_points?: {id: string; execution_id: string; temperature_K: number; voltage_per_period_V: number}[];
}
export interface ChildRun extends Run {
  execution_id: string;
  attempt: number;
  retrieved_uuid?: string;
}
export interface Resources {
  num_machines: number;
  num_mpiprocs_per_machine: number;
  num_cores_per_mpiproc: number;
  max_wallclock_seconds: number;
  max_memory_kb: number;
}
export interface DocumentationAvailability {
  status: "available" | "unavailable";
  reason: "ok" | "not_configured" | "invalid_configuration" | "bundle_missing" | "invalid_bundle";
  source_revision: string | null;
  bundle_sha256: string | null;
  url: string | null;
  journeys: { researcher: string | null; developer: string | null; operator: string | null };
}
export interface Config {
  ordinary_resources?: {
    request_selection: 'configured_request_ceiling'; allocation_authority: 'slurm';
    availability: 'not_measured'; forecast: 'advisory'; runtime_overhead: 'unknown';
    reserve: 'from_frozen_runner_policy'; confidence: 'uncalibrated';
    allocated_cpus: 'not_measured'; allocated_memory: 'not_measured';
  };
  configurator?: {available: boolean; catalog_source_id: string | null; reason: string | null; planner_version?: string | null; planner_source_id?: string | null};
  documentation?: DocumentationAvailability;
  defaults: Resources;
  profile: string;
  codes: string[];
  limits: Resources & { plan_bytes: number; artifact_bytes: number };
  exports: { disk_bytes: number; ttl_seconds: number };
}
export type ResourcesMode = 'manual' | 'automatic';
/** Serialize request intent only; the server owns the automatic CPU/RAM selection. */
export function submissionResources(mode: ResourcesMode, resources: Resources): Partial<Resources> {
  return mode === 'automatic'
    ? {max_wallclock_seconds: resources.max_wallclock_seconds} : {...resources};
}
/** Manual retains the old strict server body; automatic requires advertised support. */
export function ordinarySubmission(mode: ResourcesMode, config: Config | null,
    request: {plan: string; code_uuid: string; resources: Resources; label: string}) {
  if (mode === 'automatic' && (!config || !configuredRequest(config)))
    throw new Error('Configured automatic request ceilings are unavailable');
  return {...request, resources: submissionResources(mode, request.resources),
    ...(mode === 'automatic' ? {resources_mode: 'automatic' as const} : {})};
}
/** Configured ceilings are not a worker inventory or a measured allocation. */
export function configuredRequest(config: Config): Omit<Resources, 'max_wallclock_seconds'> | null {
  const capability = config.ordinary_resources;
  if (!capability || capability.request_selection !== 'configured_request_ceiling' ||
      capability.allocation_authority !== 'slurm' || capability.availability !== 'not_measured' ||
      capability.forecast !== 'advisory' || capability.runtime_overhead !== 'unknown' ||
      capability.reserve !== 'from_frozen_runner_policy' || capability.confidence !== 'uncalibrated' ||
      capability.allocated_cpus !== 'not_measured' || capability.allocated_memory !== 'not_measured' ||
      ![config.limits.num_cores_per_mpiproc, config.limits.max_memory_kb].every(
        value => Number.isSafeInteger(value) && value > 0)) return null;
  return {num_machines: 1, num_mpiprocs_per_machine: 1,
    num_cores_per_mpiproc: config.limits.num_cores_per_mpiproc,
    max_memory_kb: config.limits.max_memory_kb};
}
export interface Artifact {
  execution_id: string;
  attempt: number;
  calcjob_uuid: string;
  path: string;
  size: number;
}
export interface ReportEntry {
  level: string;
  message: string;
  time: string;
}
export interface AgentReportUsedRun {
  run_uuid: string;
  plan_fingerprint: string;
  execution_id: string;
  definition_id: string;
  variant_id: string;
  attempt: number;
  calcjob_uuid: string;
}
export interface AgentReport {
  schema: "qcl-negf-agent-report-v1";
  anchor: {
    run_uuid: string;
    root_definition_id: string;
    root_kind: "study" | "meta";
    plan_fingerprint: string;
  };
  question_snapshot: string;
  used_runs: AgentReportUsedRun[];
  conclusion: string;
  reasoning: string;
  limitations: string;
}
export interface AgentReportReceipt {
  uuid: string;
  filename: string;
  bytes: number;
  sha256: string;
  ctime: string;
  anchor: AgentReport["anchor"];
}
export interface ExportReceipt {
  schema: string;
  transport_schema: string;
  filename: string;
  sha256: string;
  bytes: number;
  snapshot_identity: string;
  profile: "science" | "full-state";
  complete: boolean;
}
export interface ExportSelection {
  attempt: number;
  calcjob_uuid: string;
}
export interface ExportStatus extends ExportSelection {
  export_id: string;
  execution_id: string;
  status: "running" | "completed" | "completed_with_warnings" | "failed" | "cancelled" | "paused" | "unknown";
  profile: "science" | "full-state";
  state: "preparing" | "ready" | "failed";
  progress: { phase: string; completed_bytes?: number; total_bytes?: number };
  receipt?: ExportReceipt;
  error?: string;
  expires_unix: number;
}

export function errorMessage(value: unknown): string {
  if (value instanceof Error) return value.message;
  return "The request could not be completed.";
}

export function describeDetail(value: unknown): string {
  if (typeof value === "string") return value;
  if (Array.isArray(value))
    return value
      .map((item) => {
        if (item && typeof item === "object" && "msg" in item)
          return String(item.msg);
        return "Invalid request";
      })
      .join("; ");
  return "The request was rejected.";
}

export class ApiError extends Error {
  operationId: string | null;
  outcome: string | null;
  unresolvedOperationId: string | null;
  status: number;
  constructor(message: string, headers: Headers, status = 0) {
    super(message);
    this.status = status;
    this.operationId = headers.get("X-QCL-Operation-ID");
    this.outcome = headers.get("X-QCL-Operation-Outcome");
    this.unresolvedOperationId = headers.get("X-QCL-Unresolved-Operation-ID");
  }
}

export async function api<T>(
  token: string,
  path: string,
  data?: unknown,
): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    method: data === undefined ? "GET" : "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      ...(data === undefined ? {} : { "Content-Type": "application/json" }),
    },
    body: data === undefined ? undefined : JSON.stringify(data),
    cache: "no-store",
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new ApiError(
      payload ? describeDetail(payload.detail ?? payload.reason) : `HTTP ${response.status}`,
      response.headers, response.status,
    );
  }
  return response.json() as Promise<T>;
}

export async function download(
  token: string,
  uuid: string,
  artifact: Artifact,
): Promise<void> {
  const query = new URLSearchParams({
    execution_id: artifact.execution_id,
    path: artifact.path,
    attempt: String(artifact.attempt),
    calcjob_uuid: artifact.calcjob_uuid,
  });
  await nativeDownload(token, `/runs/${uuid}/artifact/authorize?${query}`);
}

export async function prepareExport(
  token: string, uuid: string, executionId: string,
  profile: "science" | "full-state", selection?: ExportSelection,
): Promise<ExportStatus> {
  return api<ExportStatus>(token, `/runs/${uuid}/exports`, {
    execution_id: executionId, profile, ...selection,
  });
}

export async function downloadExport(token: string, exportId: string): Promise<void> {
  await nativeDownload(token, `/exports/${exportId}/authorize`);
}

async function nativeDownload(token: string, path: string): Promise<void> {
  const { url } = await api<{ url: string }>(token, path, {});
  if (!url.startsWith("/api/v1/")) throw new Error("Invalid download address.");
  const link = document.createElement("a");
  link.href = url;
  link.download = "";
  link.click();
}

export function isTerminal(run: Run): boolean {
  return ["finished", "excepted", "killed"].includes(run.process_state);
}

export function readableBytes(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KiB`;
  if (size >= 1024 * 1024 * 1024) return `${(size / 1024 / 1024 / 1024).toFixed(1)} GiB`;
  return `${(size / 1024 / 1024).toFixed(1)} MiB`;
}

export interface ModelHelpSelection {
  execution_id: string; point_id: string; attempt?: number; calcjob_uuid?: string;
}
export interface ModelHelpContext {
  run_uuid: string; calcjob_uuid: string; execution_id: string; point_id: string;
  attempt: number; plan_fingerprint: string; scientific_fingerprint: string;
  configuration_hash: string | null; model_revision: string;
}
export interface UnknownModelHelp {
  format: 'qcl-negf.model-help-view.v1'; context: ModelHelpContext;
  binding: {status: 'unknown'; reason: 'source_binding_missing'; release_id: string | null;
    release_manifest_sha256: null; source_revision: null; bundle_sha256: null};
  fragment: null; inputs: [];
  input_status: {status: 'unavailable'; reason: 'inventory_unavailable'};
}
const helpUuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?![\s\S])/;
const helpHash = /^[0-9a-f]{64}(?![\s\S])/;
const scientificId = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}(?![\s\S])/;
export const fragmentIdPattern = /^[a-z][a-z0-9._-]{0,127}(?![\s\S])/;
export function readUnknownModelHelp(value: unknown): UnknownModelHelp {
  const v = value as UnknownModelHelp | null;
  const c = v?.context; const b = v?.binding;
  if (!v || v.format !== 'qcl-negf.model-help-view.v1' || !c || !b ||
      !helpUuid.test(c.run_uuid) || !helpUuid.test(c.calcjob_uuid) ||
      !scientificId.test(c.execution_id) || !scientificId.test(c.point_id) ||
      !Number.isSafeInteger(c.attempt) || c.attempt < 1 ||
      !helpHash.test(c.plan_fingerprint) || !helpHash.test(c.scientific_fingerprint) ||
      !(c.configuration_hash === null || helpHash.test(c.configuration_hash)) ||
      typeof c.model_revision !== 'string' || !c.model_revision ||
      b.status !== 'unknown' || b.reason !== 'source_binding_missing' ||
      !(b.release_id === null || typeof b.release_id === 'string' && b.release_id.length > 0) ||
      b.source_revision !== null || b.bundle_sha256 !== null || b.release_manifest_sha256 !== null ||
      v.fragment !== null || !Array.isArray(v.inputs) || v.inputs.length !== 0 ||
      v.input_status?.status !== 'unavailable' || v.input_status.reason !== 'inventory_unavailable')
    throw new Error('Неподдерживаемый ответ контекста модели');
  return v;
}
export async function getModelHelp(token: string, runUuid: string, fragmentId: string,
    selection: ModelHelpSelection): Promise<UnknownModelHelp> {
  if (!helpUuid.test(runUuid) || !fragmentIdPattern.test(fragmentId) ||
      !scientificId.test(selection.execution_id) || !scientificId.test(selection.point_id) ||
      (selection.attempt !== undefined && (!Number.isSafeInteger(selection.attempt) || selection.attempt < 1)) ||
      (selection.calcjob_uuid !== undefined && !helpUuid.test(selection.calcjob_uuid)))
    throw new Error('Нужны точные selectors контекста');
  const query = new URLSearchParams({fragment_id: fragmentId, execution_id: selection.execution_id,
    point_id: selection.point_id});
  if (selection.attempt !== undefined) query.set('attempt', String(selection.attempt));
  if (selection.calcjob_uuid !== undefined) query.set('calcjob_uuid', selection.calcjob_uuid);
  const view = readUnknownModelHelp(await api<unknown>(token, `/runs/${runUuid}/model-help?${query}`));
  if (view.context.run_uuid !== runUuid || view.context.execution_id !== selection.execution_id ||
      view.context.point_id !== selection.point_id ||
      (selection.attempt !== undefined && view.context.attempt !== selection.attempt) ||
      (selection.calcjob_uuid !== undefined && view.context.calcjob_uuid !== selection.calcjob_uuid))
    throw new Error('Ответ относится к другому контексту');
  return view;
}

/** Small immutable card file only. No result arrays or capability credentials. */
export async function downloadResearchCard(token: string, card: string, version: string): Promise<void> {
  if (!helpUuid.test(card) || !helpUuid.test(version)) throw new Error("Canonical UUID required");
  const response = await fetch(`/api/v1/research-cards/${card}/versions/${version}/file`, {
    headers: {Authorization: `Bearer ${token}`}, cache: 'no-store',
  });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const raw = await response.arrayBuffer();
  if (raw.byteLength > 65536) throw new Error('Research card exceeds byte limit');
  const address = URL.createObjectURL(new Blob([raw], {type: 'application/json'}));
  try {
    const link = document.createElement('a'); link.href = address;
    link.download = 'research-card.json'; link.click();
  } finally { URL.revokeObjectURL(address); }
}


/** Both run-list and research-card navigation invalidate the same pending reads. */
export function selectWorkflowRun(
  target: string, selected: {current: string | null}, refreshSequence: {current: number},
  clear: () => void, select: (uuid: string) => void,
): void {
  if (target === selected.current) return;
  refreshSequence.current += 1;
  selected.current = target;
  clear();
  select(target);
}

/** Existing workflow reads, with one admission predicate for success and error. */
export async function refreshWorkflowView(
  token: string, selected: string | null, offset: number, current: () => boolean,
  view: {runs: (value: Run[]) => void; detail: (value: Run) => void;
    report: (value: ReportEntry[]) => void; artifacts: (value: Artifact[]) => void;
    error: (value: string) => void},
): Promise<void> {
  try {
    const page = await api<{runs: Run[]}>(token, `/runs?limit=25&offset=${offset}`);
    if (!current()) return;
    view.runs(page.runs);
    if (selected) {
      const [run, logs, files] = await Promise.all([
        api<Run>(token, `/runs/${selected}`),
        api<{entries: ReportEntry[]}>(token, `/runs/${selected}/report`),
        api<{artifacts: Artifact[]}>(token, `/runs/${selected}/artifacts`),
      ]);
      if (!current()) return;
      view.detail(run); view.report(logs.entries); view.artifacts(files.artifacts);
    }
  } catch (err) { if (current()) view.error(errorMessage(err)); }
}

/** Quantum-only end-to-end deadline; exact JSON bytes retained for download. */
export async function quantumPreviewRaw(token:string,body:string,signal:AbortSignal):Promise<string>{
  const controller=new AbortController();
  const cancel=()=>controller.abort();
  if(signal.aborted)cancel();else signal.addEventListener('abort',cancel,{once:true});
  const timer=setTimeout(cancel,195000);
  try{
    const response=await fetch('/api/v1/configurator/quantum-preview',{method:'POST',
      headers:{Authorization:`Bearer ${token}`,'Content-Type':'application/json'},body,
      cache:'no-store',signal:controller.signal});
    if(!response.body)throw new Error('Quantum response body unavailable');
    const reader=response.body.getReader(),chunks:Uint8Array[]=[];let bytes=0;
    while(true){const {done,value}=await reader.read();if(done)break;bytes+=value.byteLength;
      if(bytes>2097152){controller.abort();throw new Error('Quantum response exceeds 2 MiB');}chunks.push(value);}
    const raw=new Uint8Array(bytes);let offset=0;for(const chunk of chunks){raw.set(chunk,offset);offset+=chunk.byteLength;}
    const text=new TextDecoder('utf-8',{fatal:true}).decode(raw);
    if(!response.ok){
      let detail=`Quantum preview: HTTP ${response.status}`;
      try{const error:unknown=JSON.parse(text);
        if(error&&typeof error==='object'&&'reason' in error&&typeof error.reason==='string'&&/^[a-z][a-z0-9_]{0,127}$/.test(error.reason)&&'plan_changed' in error&&error.plan_changed===false){
          const actual='actual' in error&&typeof error.actual==='number'&&Number.isFinite(error.actual)?error.actual:'unknown';
          const allowed='allowed' in error&&typeof error.allowed==='number'&&Number.isFinite(error.allowed)?error.allowed:'unknown';
          detail=`${error.reason}; actual=${actual}; limit=${allowed}; frozen plan unchanged`;
        }
      }catch{/* Invalid error JSON stays a bounded HTTP refusal. */}
      throw new ApiError(detail,response.headers,response.status);
    }
    return text;
  }finally{clearTimeout(timer);signal.removeEventListener('abort',cancel);}
}
