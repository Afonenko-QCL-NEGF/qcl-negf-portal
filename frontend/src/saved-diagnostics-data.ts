import {sameSelection,retrievedSource} from './saved-results-data.ts';
import type {SavedSelection,SavedProviderSource} from './saved-results-data.ts';
export type ExactScalar=number|{kind:'integer';decimal:string};
export interface DiagnosticContext {selection:SavedSelection;source:SavedProviderSource;point_id:string;source_commit_sha256:string;metadata_sha256:string;source_attempt:number;plan_fingerprint:string;native?:Record<string,unknown>|null}
export interface HistorySelection {start:number;stop:number;step:number;columns:string[]}
export interface HistoryPhase {phase:'scba'|'outer';total_rows:ExactScalar;columns:{name:string;units:string|null;dtype:string}[];attempt_spans:Record<string,unknown>[]}
export interface Diagnostics {schema:'qcl-negf.saved-diagnostics.v1';identity:Record<string,unknown>;source_commit_sha256:string;generation:ExactScalar;closed:boolean|null;recorded_statuses:Record<string,unknown>;history:{status:string;reason:string|null;source_artifact:Record<string,unknown>|null;phases:HistoryPhase[]};resources:{status:string;reason:string|null;checkpoint:Record<string,unknown>|null;performance_summary:Record<string,unknown>};integrity:Record<string,unknown>;coverage:Record<string,unknown>;limits:Record<string,unknown>;io_counters:Record<string,unknown>}
export interface HistorySlice {schema:'qcl-negf.saved-history-slice.v1';identity:Record<string,unknown>;source_commit_sha256:string;generation:ExactScalar;closed:boolean|null;phase:'scba'|'outer';selection:HistorySelection;total_rows:ExactScalar;coordinates:{sequence:ExactScalar[];iteration:ExactScalar[];outer_iteration:ExactScalar[]|null;source_attempt:ExactScalar[]};columns:{name:string;units:string|null;values:(ExactScalar|null)[];invalid_mask:(string|null)[]}[];break_before:boolean[];recorded_statuses:Record<string,unknown>;coverage:Record<string,unknown>;integrity:Record<string,unknown>;io_counters:Record<string,unknown>}
type Row=Record<string,unknown>;
const object=(v:unknown):v is Row=>!!v&&typeof v==='object'&&!Array.isArray(v);
const finite=(v:unknown):v is number=>typeof v==='number'&&Number.isFinite(v);
const count=(v:unknown):v is number=>Number.isSafeInteger(v)&&Number(v)>=0;
const tagged=(v:unknown):v is {kind:'integer';decimal:string}=>object(v)&&Object.keys(v).sort().join(',')==='decimal,kind'&&v.kind==='integer'&&typeof v.decimal==='string'&&/^-?(?:0|[1-9][0-9]*)$/.test(v.decimal)&&v.decimal.length<=4096;
const integer=(v:unknown):v is ExactScalar=>count(v)||tagged(v);
function require(ok:unknown):asserts ok{if(!ok)throw new Error('Некорректная или устаревшая saved diagnostics projection');}
function same(a:unknown,b:unknown):boolean {if(object(a)&&object(b)){const keys=Object.keys(a).sort();return keys.join('\0')===Object.keys(b).sort().join('\0')&&keys.every(k=>same(a[k],b[k]));}if(Array.isArray(a)&&Array.isArray(b))return a.length===b.length&&a.every((v,i)=>same(v,b[i]));return a===b;}
function bounded(value:unknown){let count=0;const todo:[unknown,number][]=[[value,0]];while(todo.length){const [v,d]=todo.pop()!;require(++count<=300000&&d<=64);if(typeof v==='number')require(finite(v));else if(Array.isArray(v))for(const x of v)todo.push([x,d+1]);else if(object(v))for(const x of Object.values(v))todo.push([x,d+1]);else require(v===null||typeof v==='string'||typeof v==='boolean');}require(new TextEncoder().encode(JSON.stringify(value)).byteLength<=2097152);}
function binding(value:unknown,context:DiagnosticContext):asserts value is Row & {integrity:Row} {
 bounded(value);require(object(value)&&sameSelection(value.provider_selection,context.selection)&&same(value.provider_source,context.source)&&value.source_commit_sha256===context.source_commit_sha256&&value.metadata_sha256===context.metadata_sha256&&value.point_id===context.point_id&&value.source_attempt===context.source_attempt&&object(value.identity));
 require(value.identity.point_id===context.point_id&&value.identity.execution_id===context.selection.execution_id&&value.identity.attempt===context.source_attempt&&value.identity.plan_fingerprint===context.plan_fingerprint);
 if(context.source.provider!=='aiida_retrieved')require(context.native&&same(value.source_native_identity,context.native)&&same(value.identity,context.native));
 require(integer(value.generation)&&(value.closed===null||typeof value.closed==='boolean')&&object(value.recorded_statuses)&&object(value.integrity)&&object(value.coverage)&&object(value.io_counters));
 require(value.integrity.metadata_checked===true&&value.integrity.native_validation==='not_performed'&&value.coverage.scientific_acceptance==='not_assessed');
}
export function exactText(value:unknown):string {return tagged(value)?value.decimal:value===null||value===undefined?'unknown':typeof value==='object'?JSON.stringify(value):String(value);}
export function readDiagnostics(value:unknown,context:DiagnosticContext):Diagnostics {
 binding(value,context);require(value.schema==='qcl-negf.saved-diagnostics.v1'&&object(value.history)&&object(value.resources)&&object(value.limits));
 const history=value.history;require(['available','unavailable'].includes(String(history.status))&&(history.reason===null||typeof history.reason==='string')&&Array.isArray(history.phases)&&history.phases.length<=2&&value.integrity.owner_sha_verified===false);
 const phases=new Set();for(const phase of history.phases){require(object(phase)&&['scba','outer'].includes(String(phase.phase))&&!phases.has(phase.phase)&&integer(phase.total_rows)&&Array.isArray(phase.columns)&&phase.columns.length<=4096&&Array.isArray(phase.attempt_spans)&&phase.attempt_spans.length<=4096);phases.add(phase.phase);const names=new Set();for(const column of phase.columns){require(object(column)&&typeof column.name==='string'&&column.name.length<=128&&!names.has(column.name)&&(column.units===null||typeof column.units==='string')&&typeof column.dtype==='string');names.add(column.name);}}
 if(history.status==='available')require(object(history.source_artifact)&&history.source_artifact.role==='science.history'&&history.source_artifact.schema==='qcl-negf-scientific-history-v4'&&typeof history.source_artifact.artifact_id==='string'&&integer(history.source_artifact.bytes)&&typeof history.source_artifact.sha256==='string'&&/^[a-f0-9]{64}$/.test(history.source_artifact.sha256));
 else require(typeof history.reason==='string'&&history.phases.length===0&&history.source_artifact===null);
 require(['available','unavailable'].includes(String(value.resources.status))&&(value.resources.reason===null||typeof value.resources.reason==='string'));
 require(value.resources.checkpoint===null||object(value.resources.checkpoint));
 if(object(value.resources.checkpoint)){const checkpoint=value.resources.checkpoint;require(checkpoint.scope==='point_commit_checkpoint_payload'&&(checkpoint.checkpoint_payload_seconds===null||finite(checkpoint.checkpoint_payload_seconds))&&(checkpoint.checkpoint_payload_bytes===null||integer(checkpoint.checkpoint_payload_bytes))&&['includes_commit_publication','jit_separately_measured'].every(k=>checkpoint[k]===null||typeof checkpoint[k]==='boolean'));}
require(object(value.resources.performance_summary)&&value.resources.performance_summary.status==='unavailable'&&value.resources.performance_summary.reason==='source_binding_missing');
 return value as unknown as Diagnostics;
}
export function historySelection(start:string,stop:string,step:string,columns:string[]):HistorySelection {
 const parse=(raw:string)=>{require(/^(?:0|[1-9][0-9]*)$/.test(raw));const n=Number(raw);require(count(n));return n;};
 const result={start:parse(start),stop:parse(stop),step:parse(step),columns:[...columns]};require(result.start<result.stop&&result.step>0&&Math.ceil((result.stop-result.start)/result.step)<=512&&columns.length>=1&&columns.length<=16&&new Set(columns).size===columns.length);return result;
}
export function readHistory(value:unknown,context:DiagnosticContext,descriptor:Diagnostics,phase:'scba'|'outer',selection:HistorySelection):HistorySlice {
 binding(value,context);require(value.schema==='qcl-negf.saved-history-slice.v1'&&value.phase===phase&&same(value.identity,descriptor.identity)&&same(value.generation,descriptor.generation)&&value.closed===descriptor.closed&&same(value.source_artifact,descriptor.history.source_artifact)&&same(value.selection,selection)&&integer(value.total_rows)&&object(value.coordinates)&&Array.isArray(value.columns)&&value.columns.length===selection.columns.length&&Array.isArray(value.break_before)&&value.integrity.owner_sha_verified===true);
 const n=Math.ceil((selection.stop-selection.start)/selection.step);require(n<=512&&value.break_before.length===n&&value.break_before.every(v=>typeof v==='boolean'));
 for(const key of ['sequence','iteration','source_attempt'])require(Array.isArray(value.coordinates[key])&&(value.coordinates[key] as unknown[]).length===n&&(value.coordinates[key] as unknown[]).every(integer));
 require(phase==='scba'?Array.isArray(value.coordinates.outer_iteration)&&value.coordinates.outer_iteration.length===n&&value.coordinates.outer_iteration.every(integer):value.coordinates.outer_iteration===null);
 const known=descriptor.history.phases.find(p=>p.phase===phase);require(known&&same(known.total_rows,value.total_rows));
 for(let i=0;i<value.columns.length;i++){const column=value.columns[i];require(object(column)&&column.name===selection.columns[i]&&known.columns.some(c=>c.name===column.name&&c.units===column.units)&&Array.isArray(column.values)&&Array.isArray(column.invalid_mask)&&column.values.length===n&&column.invalid_mask.length===n);for(let j=0;j<n;j++)require(column.values[j]===null?['nan','positive_infinity','negative_infinity'].includes(String(column.invalid_mask[j])):(finite(column.values[j])||tagged(column.values[j]))&&column.invalid_mask[j]===null);}
 return value as unknown as HistorySlice;
}
/** Preserve owning breaks, unsafe exact coordinates and invalid samples as gaps. */
export function historyPaths(data:HistorySlice,column:string,axis:'iteration'|'sequence'):number[][][] {
 const values=data.columns.find(c=>c.name===column);if(!values)return [];
 const paths:number[][][]=[];let active:number[][]=[];const flush=()=>{if(active.length)paths.push(active);active=[];};
 values.values.forEach((value,i)=>{if(data.break_before[i]||data.selection.step>1)flush();const x=data.coordinates[axis][i];if(!finite(x)||!finite(value)||values.invalid_mask[i]!==null){flush();return;}active.push([x,value]);});flush();return paths;
}
export {retrievedSource};
