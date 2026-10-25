import assert from 'node:assert/strict';
import {test} from 'node:test';
import {cellEdges, currentPaths, readBrief, readSlice, SavedSession, selectionOptions} from '../src/saved-results-data.ts';
const selection={run_uuid:'run',execution_id:'ex',attempt:2,calcjob_uuid:'job'};
const hash='a'.repeat(64);
const points=[0,1,2].map(i=>({point_id:`p${i}`,coordinates:{voltage_per_period_V:i*.01,temperature_K:70,branch:'b'},source_commit_sha256:hash,source_attempt:2,scalar_observables:i===1?{}:{current_density_A_per_m2:i+1},recorded_statuses:{scientific_accepted:i!==1},missing:i===1?['current_unavailable']:[]}));
const brief={schema:'qcl-negf.saved-results.v1',selection,provider_selection:selection,metadata_sha256:hash,points,current_segments:[['p0'],['p2']],total:3,next_cursor:null,complete_page:true,identity:{},statuses:{scientific_accepted:false},source:{kind:'aiida_retrieved'}};

test('literal missing J gap has isolated endpoints; no derived topology',()=>{
  const b=readBrief(brief,selection);
  assert.deepEqual(currentPaths(b),[[[0,1]],[[.02,3]]]);
  assert.throws(()=>readBrief({...brief,provider_selection:{...selection,attempt:1}},selection));
  assert.throws(()=>readBrief({...brief,metadata_sha256:'wrong'},selection));
});
test('nonuniform native coordinate cells and signed invalid finite zero stay distinct',()=>{
  const edges=cellEdges([-.2,.1,.6]);[-.35,-.05,.35,.85].forEach((v,i)=>assert.ok(Math.abs(edges[i]-v)<1e-12));
  assert.deepEqual(cellEdges([0,1,4,9]),[-.5,.5,2.5,6.5,11.5]);
  const value={provider_selection:selection,selection,metadata_sha256:hash,point_id:'p0',source_attempt:2,source_commit_sha256:hash,artifact_sha256:hash,view_id:'spatial_energy_density',returned_shape:[2,2],logical_axes:['E','z'],axes:[{name:'E',values:[-.2,.6],units:'eV'},{name:'z',values:[0,9],units:'nm'}],values:[[-11,0],[null,21]],invalid_mask:[[null,null],['nan',null]],units:'m^-3/eV',conventions:{},recorded_statuses:{scientific_accepted:false}};
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
