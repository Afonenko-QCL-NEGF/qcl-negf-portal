import assert from 'node:assert/strict';
import {test} from 'node:test';
import {readDiagnostics,readHistory,historyPaths,historySelection,exactText} from '../src/saved-diagnostics-data.ts';
const hash='a'.repeat(64),selection={run_uuid:'run',execution_id:'ex',attempt:2,calcjob_uuid:'job'},source={provider:'aiida_retrieved' as const,source_uuid:null,source_sha256:null};
const context={selection,source,point_id:'p',source_commit_sha256:hash,metadata_sha256:hash,source_attempt:1,plan_fingerprint:hash};
const identity={point_id:'p',execution_id:'ex',attempt:1,plan_fingerprint:hash};
const common={provider_selection:selection,provider_source:source,point_id:'p',source_attempt:1,identity,source_commit_sha256:hash,metadata_sha256:hash,generation:7,closed:null,recorded_statuses:{terminal_status:'failed',scientific_accepted:null},integrity:{metadata_checked:true,owner_sha_verified:false,native_validation:'not_performed'},coverage:{scientific_acceptance:'not_assessed'},io_counters:{},limits:{}};
const descriptor={...common,schema:'qcl-negf.saved-diagnostics.v1',history:{status:'available',reason:null,source_artifact:{artifact_id:'history.h5',role:'science.history',schema:'qcl-negf-scientific-history-v4',bytes:100,sha256:hash},phases:[{phase:'scba',total_rows:5,columns:[{name:'r_K',units:null,dtype:'float64'}],attempt_spans:[]}]},resources:{status:'available',reason:null,checkpoint:{checkpoint_payload_bytes:{kind:'integer',decimal:'9007199254740993'},checkpoint_payload_seconds:2,includes_commit_publication:false,jit_separately_measured:false,scope:'point_commit_checkpoint_payload'},performance_summary:{status:'unavailable',reason:'source_binding_missing'}}};
test('exact descriptor binding, failed unknown acceptance and large resource tags stay raw',()=>{
 const data=readDiagnostics(descriptor,context);assert.equal(data.closed,null);assert.equal(exactText(data.resources.checkpoint?.checkpoint_payload_bytes),'9007199254740993');
 for(const bad of [{provider_selection:{...selection,attempt:1}},{source_commit_sha256:'b'.repeat(64)},{identity:{...identity,attempt:2}},{provider_source:{...source,source_uuid:'foreign'}}])assert.throws(()=>readDiagnostics({...descriptor,...bad},context));
});
test('native coordinates, masks and cycle breaks are independent literal oracles',()=>{
 const data=readDiagnostics(descriptor,context),requested=historySelection('0','5','1',['r_K']);
 const raw={...common,schema:'qcl-negf.saved-history-slice.v1',source_artifact:descriptor.history.source_artifact,phase:'scba',selection:requested,total_rows:5,coordinates:{sequence:[100,101,102,103,104],iteration:[1,2,3,1,2],outer_iteration:[1,1,1,2,2],source_attempt:[1,1,1,1,1]},columns:[{name:'r_K',units:null,values:[.1,.001,null,.2,.02],invalid_mask:[null,null,'nan',null,null]}],break_before:[true,false,true,true,false],integrity:{...common.integrity,owner_sha_verified:true}};
 const result=readHistory(raw,context,data,'scba',requested);
 assert.deepEqual(historyPaths(result,'r_K','sequence'),[[[100,.1],[101,.001]],[[103,.2],[104,.02]]]);
 assert.deepEqual(historyPaths(result,'r_K','iteration'),[[[1,.1],[2,.001]],[[1,.2],[2,.02]]]);
 assert.throws(()=>readHistory({...raw,columns:[{...raw.columns[0],invalid_mask:[null,null,null,null,null]}]},context,data,'scba',requested));
 const exact={...result,coordinates:{...result.coordinates,sequence:[{kind:'integer' as const,decimal:'9007199254740993'},101,102,103,104]}};
 assert.deepEqual(historyPaths(exact,'r_K','sequence'),[[[101,.001]],[[103,.2],[104,.02]]]);
});
test('no implicit history selection, no duplicate columns or oversized pages',()=>{
 for(const values of [['','5','1'],['0','513','1'],['0','5','0'],['1.0','5','1'],['0','5','1e0']])assert.throws(()=>historySelection(values[0],values[1],values[2],['r_K']));
 assert.throws(()=>historySelection('0','5','1',['r_K','r_K']));
});
