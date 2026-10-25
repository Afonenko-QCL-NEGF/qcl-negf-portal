import assert from 'node:assert/strict';
import {test} from 'node:test';
import {cellEdges, currentPaths, readBrief, readSlice, SavedSession, selectionOptions} from '../src/saved-results-data.ts';
const selection={run_uuid:'run',execution_id:'ex',attempt:2,calcjob_uuid:'job'};
const hash='a'.repeat(64);
const points=[0,1,2].map(i=>({point_id:`p${i}`,coordinates:{voltage_per_period_V:i*.01,temperature_K:70,branch:'b'},source_commit_sha256:hash,source_attempt:2,scalar_observables:i===1?{}:{current_density_A_per_m2:i+1},recorded_statuses:{scientific_accepted:i!==1},missing:i===1?['current_unavailable']:[]}));
const retrieved={provider:'aiida_retrieved',source_uuid:null,source_sha256:null};
const brief={provider_source:retrieved,schema:'qcl-negf.saved-results.v1',selection,provider_selection:selection,metadata_sha256:hash,points,current_segments:[['p0'],['p2']],total:3,next_cursor:null,complete_page:true,identity:{},statuses:{scientific_accepted:false},source:{kind:'aiida_retrieved'}};

test('literal missing J gap has isolated endpoints; no derived topology',()=>{
  const b=readBrief(brief,selection);
  assert.deepEqual(currentPaths(b),[[[0,1]],[[.02,3]]]);
  assert.throws(()=>readBrief({...brief,provider_selection:{...selection,attempt:1}},selection));
  assert.throws(()=>readBrief({...brief,metadata_sha256:'wrong'},selection));
});
test('nonuniform native coordinate cells and signed invalid finite zero stay distinct',()=>{
  const edges=cellEdges([-.2,.1,.6]);[-.35,-.05,.35,.85].forEach((v,i)=>assert.ok(Math.abs(edges[i]-v)<1e-12));
  assert.deepEqual(cellEdges([0,1,4,9]),[-.5,.5,2.5,6.5,11.5]);
  const value={provider_source:retrieved,provider_selection:selection,selection,metadata_sha256:hash,point_id:'p0',source_attempt:2,source_commit_sha256:hash,artifact_sha256:hash,view_id:'spatial_energy_density',returned_shape:[2,2],logical_axes:['E','z'],axes:[{name:'E',values:[-.2,.6],units:'eV'},{name:'z',values:[0,9],units:'nm'}],values:[[-11,0],[null,21]],invalid_mask:[[null,null],['nan',null]],units:'m^-3/eV',conventions:{},recorded_statuses:{scientific_accepted:false}};
  assert.deepEqual(readSlice(value,selection,'p0',hash,'spatial_energy_density').values,[[-11,0],[null,21]]);
  assert.throws(()=>readSlice({...value,values:[[Infinity,0],[null,21]]},selection,'p0',hash,'spatial_energy_density'));
  assert.throws(()=>readSlice({...value,invalid_mask:[[null,null],[null,null]]},selection,'p0',hash,'spatial_energy_density'));
  assert.throws(()=>readSlice({...value,source_commit_sha256:'b'.repeat(64)},selection,'p0',hash,'spatial_energy_density'));
});
test('explicit choices retain failed and historical children without first-child default',()=>{
  assert.deepEqual(selectionOptions({uuid:'r',children:[{uuid:'job-old',execution_id:'ex',attempt:1},{uuid:'job-failed',execution_id:'ex',attempt:2}]}),[
    {run_uuid:'r',execution_id:'ex',attempt:1,calcjob_uuid:'job-old'},
    {run_uuid:'r',execution_id:'ex',attempt:2,calcjob_uuid:'job-failed'}]);
  assert.deepEqual(selectionOptions({uuid:'r'}),[]);
});
test('actual async fence discards late success/error after provenance change',async()=>{
  const session=new SavedSession();let finish!:(v:unknown)=>void;let shown='';
  const old=session.begin();const waiting=session.receive(old,()=>new Promise(ok=>finish=ok),v=>shown=String(v),e=>shown=String(e));
  session.invalidate();const next=session.begin();await session.receive(next,async()=> 'new',v=>shown=String(v),()=>{});finish('old');await waiting;
  assert.equal(shown,'new');
});

test('display-only unit scaling stays finite for opposite extremes and a huge singleton',async()=>{
 const {displayScale}=await import('../src/saved-results-data.ts');
 const signed=displayScale([-1e308,0,1e308]);
 assert.deepEqual([-1e308,0,1e308].map(signed.position),[0,.5,1]);
 assert.equal(displayScale([1e308]).position(1e308),.5);
 const edges=cellEdges([1e308,1.5e308]);[7.5e307,1.25e308,1.75e308].forEach((v,i)=>assert.ok(Number.isFinite(edges[i])&&Math.abs((edges[i]-v)/v)<2e-15));
 assert.throws(()=>cellEdges([-1e308,1e308]),/geometry/);
 assert.throws(()=>cellEdges([1e308]),/geometry/);
});

test('source A and B identical SHA remain distinct; imported native selection never stamped current',async()=>{
 const {readManifest,readSources}=await import('../src/saved-results-data.ts');
 const a={provider:'nfs_registered' as const,source_uuid:'44444444-4444-4444-8444-444444444444',source_sha256:hash};
 const b={...a,source_uuid:'55555555-5555-4555-8555-555555555555'};
 const native={point_id:'p0',execution_id:'ex',attempt:1,plan_fingerprint:hash,run_uuid:'producer-run'};
 const point={...points[0],source_attempt:1,source_native_identity:native,source_calcjob_uuid:null,analysis_source_status:'registered'};
 const nfs={...brief,identity:{plan_fingerprint:hash},provider_source:a,points:[point],current_segments:[['p0']],total:1,snapshot_complete:false,registered_point_ids:['p0']};
 assert.equal(readBrief(nfs,selection,a).points[0].source_native_identity?.attempt,1);
 assert.throws(()=>readBrief(nfs,selection,b));
 const manifest={provider_selection:selection,provider_source:a,metadata_sha256:hash,point_id:'p0',source_attempt:1,source_commit_sha256:hash,source_native_identity:native,source_calcjob_uuid:null,views:[],files:[],integrity:{}};
 readManifest(manifest,selection,'p0',hash,hash,a,point);
 assert.throws(()=>readManifest({...manifest,source_native_identity:{...native,run_uuid:'run'}},selection,'p0',hash,hash,a,point));
 const value={...manifest,selection:{run_uuid:'producer-run',execution_id:'ex',attempt:1,calcjob_uuid:null},artifact_sha256:hash,view_id:'density',returned_shape:[1],logical_axes:['z'],axes:[{name:'z',values:[7],units:'nm'}],values:[-11],invalid_mask:[null],units:'m^-3',conventions:{},recorded_statuses:{}};
 assert.deepEqual(readSlice(value,selection,'p0',hash,'density',hash,a,manifest).selection,value.selection);
 assert.throws(()=>readSlice({...value,selection:{...selection,attempt:1}},selection,'p0',hash,'density',hash,a,manifest));
 const catalog={selection,provider_selection:selection,provider:'nfs_registered',complete:true,storage_binding_status:'unavailable',sources:[{status:'available',selection,source_uuid:a.source_uuid,source_sha256:hash,metadata_status:'verified',copy_status:'actual',access_status:'not_checked'}]};
 assert.equal(readSources(catalog,selection).sources.length,1);
 assert.throws(()=>readSources({...catalog,sources:[catalog.sources[0],catalog.sources[0]]},selection));
});
test('provider and source changes invalidate late success AND error',async()=>{
 const session=new SavedSession();let done!:(v:unknown)=>void,failed!:(e:unknown)=>void;const shown:string[]=[];
 const old=session.begin();const success=session.receive(old,()=>new Promise(ok=>done=ok),()=>shown.push('A'),()=>shown.push('A-error'));
 session.invalidate();const pending=session.begin();const error=session.receive(pending,()=>new Promise((_,no)=>failed=no),()=>shown.push('B'),()=>shown.push('B-error'));
 session.invalidate();done('A');failed(new Error('secret old source path'));await Promise.all([success,error]);assert.deepEqual(shown,[]);
});
