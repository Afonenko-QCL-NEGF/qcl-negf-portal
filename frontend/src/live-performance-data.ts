import {boundedNative,sameValue} from './saved-results-data.ts';
export type RecordValue=Record<string,unknown>;
export const object=(v:unknown):v is RecordValue=>v!==null&&typeof v==='object'&&!Array.isArray(v);
export interface PerformanceSelection {mode:'retained'|'current';anchor_key:string}
const keys=(v:RecordValue,names:string)=>Object.keys(v).sort().join(',')===names.split(',').sort().join(',');
export function checkedLive(value:unknown,exact:RecordValue,kind:string,request?:PerformanceSelection):RecordValue{
 if(!object(value)||!object(value.provider_selection)||!sameValue(value.provider_selection,exact))throw new Error('Exact provider identity required');
 if(kind!=='performance'){
  if(value.scope!=='closed_prefix'||value.descriptor_verified!==true||typeof value.index_sha256!=='string'||!Number.isSafeInteger(value.generation)||!object(value.cutoff))throw new Error('Verified closed prefix required');
  return value;
 }
 boundedNative(value);
 if(!request||!keys(value,'schema,operation,status,provider_selection,mode,anchor_key,source_metadata,access,view,owner_runtime')||value.schema!=='qcl-negf.portal-performance.v2'||value.operation!=='performance'||value.mode!==request.mode||value.anchor_key!==request.anchor_key||! /^[a-f0-9]{64}$/.test(request.anchor_key))throw new Error('Exact performance mode and anchor required');
 const runtime={scope:'aiida_public_source_getters',status:'not_measured',actual_io_bytes:null,heap_bytes:null,peak_rss_bytes:null,reason:'not_reported_by_public_source_getters'};
 if(!sameValue(value.owner_runtime,runtime))throw new Error('Unknown actor measurements required');
 const source=value.source_metadata;
 if(!object(source)||source.schema!=='qcl-negf.performance-source.v2'||source.status!==value.status||!sameValue(source.provider_selection,exact))throw new Error('Whole owning source required');
 if(value.status==='staging'||value.status==='unavailable'){
  if(typeof source.reason!=='string'||!Array.isArray(source.candidates)||value.view!==null||value.access!==null||'locator' in source)throw new Error('Owning refusal without partial view required');
  return value;
 }
 const view=value.view,entry=source.entry,origin=source.origin,access=value.access;
 if(value.status!=='available'||source.anchor_key!==request.anchor_key||!object(origin)||origin.mode!==request.mode||!object(entry)||entry.anchor_key!==request.anchor_key||!object(source.index)||typeof source.index.raw_utf8!=='string'||!object(view)||view.schema!=='qcl-negf.performance-summary-view.v2'||view.status!=='available'||view.scope!=='execution_attempt'||!object(view.summary)||!object(view.io_counters)||!object(view.observer_self_cost)||!object(access)||!keys(access,'status,method,config_sha256,scope')||access.status!=='checked_ro_current_request'||access.method!=='local_binding_mountinfo_nofollow_domain'||access.scope!=='selected_scientific_and_technical_roots'||typeof access.config_sha256!=='string'||!/^[a-f0-9]{64}$/.test(access.config_sha256))throw new Error('Qualified performance source and public v2 view required');
 const index:unknown=JSON.parse(source.index.raw_utf8);
 if(!object(index)||!Array.isArray(index.entries)||index.entries.filter(v=>sameValue(v,entry)).length!==1||typeof entry.runner_anchor_utf8!=='string'||!object(entry.telemetry))throw new Error('Selected whole index entry required');
 const anchor:unknown=JSON.parse(entry.runner_anchor_utf8);
 if(!object(anchor)||!object(anchor.scientific)||!sameValue(view.source_state,anchor.scientific.selected_state)||!sameValue(view.transport,entry.telemetry.expected_transport))throw new Error('Exact native StateRef and transport required');
 return value;
}
