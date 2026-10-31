import { ApiError, describeDetail, type AgentReport } from './api.ts';
import { mutationUncertain } from './configurator-data.ts';

export const REPORT_MAX_BYTES = 262144;
export type Anchor = {run_uuid:string; root_definition_id:string; root_kind:string; plan_fingerprint:string};
export type ReportPreview = {raw:string; bytes:number; sha256:string; anchor:Anchor};
const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

export async function prepareReport(file: Pick<File, 'size'|'arrayBuffer'>, run: string):Promise<ReportPreview> {
  if (file.size > REPORT_MAX_BYTES) throw new Error('Report exceeds 262144 bytes');
  const bytes = new Uint8Array(await file.arrayBuffer());
  if (bytes.length !== file.size || bytes.length > REPORT_MAX_BYTES) throw new Error('Invalid report size');
  if (bytes[0] === 0xef && bytes[1] === 0xbb && bytes[2] === 0xbf) throw new Error('UTF-8 BOM is not supported');
  const raw = new TextDecoder('utf-8', {fatal:true}).decode(bytes);
  const encoded = new TextEncoder().encode(raw);
  if (encoded.length !== bytes.length || encoded.some((b,i) => b !== bytes[i])) throw new Error('Report bytes cannot roundtrip');
  const parsed:unknown = JSON.parse(raw);
  const obj = parsed as {anchor?:Record<string,unknown>} | null;
  const a = obj?.anchor;
  if (!a || typeof a.run_uuid !== 'string' || a.run_uuid !== run ||
      typeof a.root_definition_id !== 'string' || typeof a.root_kind !== 'string' ||
      typeof a.plan_fingerprint !== 'string') throw new Error('Report anchor does not match selected run');
  const anchor:Anchor = {run_uuid:a.run_uuid, root_definition_id:a.root_definition_id,
    root_kind:a.root_kind, plan_fingerprint:a.plan_fingerprint};
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  const sha256 = Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2,'0')).join('');
  return Object.freeze({raw,bytes:bytes.length,sha256,anchor:Object.freeze(anchor)});
}
export type ReportSelection = Readonly<{token:string; run:string; preview:ReportPreview}>;
export class ReportGate {
  private busy = false;
  private unknown = false;
  private active:ReportSelection | null = null;
  private consumed = new WeakSet<ReportPreview>();
  private listeners = new Set<()=>void>();
  diagnostic = '';
  subscribe(fn:()=>void) { this.listeners.add(fn); return () => {this.listeners.delete(fn);}; }
  private notify() { this.listeners.forEach(fn => fn()); }
  get blocked() { return this.busy || this.unknown; }
  get uncertain() { return this.unknown; }
  begin(selection:ReportSelection) { if (this.blocked || this.consumed.has(selection.preview)) return false; this.busy=true; this.active=Object.freeze(selection); this.notify(); return this.active; }
  finish(error?:unknown) {
    if (error !== undefined && mutationUncertain(error)) {
      this.unknown=true;
      if (error instanceof ApiError) this.diagnostic = [error.operationId,error.unresolvedOperationId].filter(Boolean).join(' / ');
    }
    if (error === undefined && this.active) this.consumed.add(this.active.preview);
    this.active=null; this.busy=false; this.notify();
  }
}
export function validateReceipt(value:unknown, selection:ReportSelection):string {
  const r=value as {uuid?:unknown;filename?:unknown;bytes?:unknown;sha256?:unknown;anchor?:Record<string,unknown>} | null;
  const a=selection.preview.anchor;
  if (!r || typeof r.uuid !== 'string' || !uuidPattern.test(r.uuid) || r.filename !== 'agent-report.json' ||
      r.bytes !== selection.preview.bytes || r.sha256 !== selection.preview.sha256 || !r.anchor ||
      Object.keys(a).some(k => r.anchor?.[k] !== a[k as keyof Anchor])) throw new Error('Invalid report receipt; outcome unknown');
  return r.uuid;
}
export async function postReport(selection:ReportSelection):Promise<string> {
  const response = await fetch(`/api/v1/runs/${encodeURIComponent(selection.run)}/agent-reports`, {
    method:'POST', headers:{Authorization:`Bearer ${selection.token}`,'Content-Type':'application/json'},
    cache:'no-store', body:JSON.stringify({report:selection.preview.raw})});
  if (!response.ok) {
    const payload=await response.json().catch(()=>null);
    throw new ApiError(payload ? describeDetail(payload.detail ?? payload.reason) : `HTTP ${response.status}`,
      response.headers,response.status);
  }
  if (response.status !== 201) throw new Error('Unexpected report response; outcome unknown');
  const receipt:unknown = await response.json();
  return validateReceipt(receipt,selection);
}
export async function getRawReport(token:string,run:string,id:string):Promise<string> {
  if (!uuidPattern.test(id)) throw new Error('Invalid report UUID');
  const response=await fetch(`/api/v1/runs/${encodeURIComponent(run)}/agent-reports/${id}`,
    {headers:{Authorization:`Bearer ${token}`},cache:'no-store'});
  if (!response.ok) throw new Error(`Report read: HTTP ${response.status}`);
  const data=new Uint8Array(await response.arrayBuffer());
  if (data.length > REPORT_MAX_BYTES) throw new Error('Report exceeds byte limit');
  return new TextDecoder('utf-8',{fatal:true,ignoreBOM:true}).decode(data);
}

/** Friendly rendering is admitted only when every displayed attempt is exact. */
export function friendlyReport(raw:string):AgentReport|null {
  try {
    const v:unknown=JSON.parse(raw);
    if (!v || typeof v !== 'object') return null;
    const report=v as AgentReport;
    if (!report.anchor || typeof report.anchor.run_uuid !== 'string' ||
        typeof report.anchor.root_definition_id !== 'string' || typeof report.anchor.root_kind !== 'string' ||
        typeof report.anchor.plan_fingerprint !== 'string' || !Array.isArray(report.used_runs) ||
        ![report.question_snapshot,report.conclusion,report.reasoning,report.limitations].every(x=>typeof x==='string') ||
        !report.used_runs.every(r=>r && Number.isSafeInteger(r.attempt) &&
          [r.run_uuid,r.plan_fingerprint,r.execution_id,r.definition_id,r.variant_id,r.calcjob_uuid].every(x=>typeof x==='string'))) return null;
    return report;
  } catch { return null; }
}
