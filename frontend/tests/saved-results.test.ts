import assert from 'node:assert/strict';
import {test} from 'node:test';
import {cellEdges,displayScale,readManifest,readSlice,readNativeEnvelope,nativePin,SavedSession,selectionOptions} from '../src/saved-results-data.ts';
import type {NativeContext,StateRef} from '../src/saved-results-data.ts';
export function nativeFixture(){
 const state:StateRef={identity:{run_id:'native',execution_id:'exec',point_id:'point',attempt:'18446744073709551615',branch_id:'branch',model_sha256:'b'.repeat(64),plan_sha256:'c'.repeat(64)},state_id:'state',state_sequence:'7',manifest:{path:'state.json',bytes:'12',sha256:'a'.repeat(64),schema:'qcl-negf.result-manifest.v1',media_type:'application/json'}};
 const selection={run_uuid:'11111111-1111-4111-8111-111111111111',execution_id:'exec',attempt:2,calcjob_uuid:'22222222-2222-4222-8222-222222222222'};
 const pin={node_uuid:'33333333-3333-4333-8333-333333333333',bytes:'2',sha256:'d'.repeat(64)};
 const context:NativeContext={selection,point_id:'point',native_binding_uuid:pin.node_uuid,state_manifest_sha256:state.manifest.sha256};
 const response={schema:'qcl-negf.portal-selected-reader.v2',operation:'manifest',provider_selection:selection,provider:'native_nfs',selection_mode:'retained',source_state:state,retained:{native_result:pin,native_binding:pin,creator:null,integrity:{readback:{native_contexts:'not_reverified',current_access:'not_checked',scientific_acceptance:'not_assessed'},original_binding:{scope:'selected_metadata',native_contexts:'verified',transport_owner:'verified',full_core_payloads:'not_assessed',current_access:'not_checked'}}},access:{status:'checked_ro_current_request',method:'local_binding_mountinfo_nofollow_domain',config_sha256:'e'.repeat(64)},view:{source_state:state,views:{density:{view_id:'density',availability:'available',shape:[3],axes:['z'],units:'m^-3',basis:null,allowed_selection:{axis_count:1}}}}};
 return {state,selection,pin,context,response};
}
test('native5 envelope binds full source and original observation dictionary without attempt alias',()=>{
 const {state,selection,pin,context,response}=nativeFixture();assert.deepEqual(readManifest(response,context).source_state,state);
 for(const bad of [{...response,provider_selection:{...selection,attempt:1}},{...response,source_state:{...state,state_id:'foreign'}},{...response,retained:{...response.retained,native_binding:{...pin,node_uuid:selection.calcjob_uuid}}}])assert.throws(()=>readNativeEnvelope(bad,{...context,state_ref:state},'manifest'));
 assert.ok(!('run_uuid' in nativePin(context)));assert.throws(()=>readNativeEnvelope({...response,selection_mode:'current'},context,'manifest'));
});
test('native slice preserves signed zero and all native invalid masks with exact axes',()=>{
 const {response,context,state}=nativeFixture();const view={source_state:state,view_id:'density',returned_shape:[3],logical_axes:['z'],axes:[{name:'z',values:[0,2,7],units:'nm',weights:[.1,.2,.7]}],values:[-0,-2,null],invalid_mask:[null,null,'positive_infinity'],units:'m^-3',conventions:{},recorded_statuses:{scientific_accepted:null}};
 const value=readSlice({...response,operation:'slice',view},{...context,state_ref:state},'density').view;
 assert.ok(Object.is((value.values as number[])[0],-0));assert.deepEqual(value.axes[0].values,[0,2,7]);
 assert.throws(()=>readSlice({...response,operation:'slice',view:{...view,invalid_mask:[null,null,null]}},context,'density'));
});
test('actual generation fence discards late success and failure after native source change',async()=>{
 const session=new SavedSession();let apply='B';let resolve!:(v:string)=>void;const old=session.begin();const pending=session.receive(old,()=>new Promise<string>(r=>resolve=r),v=>apply=v,()=>apply='error');session.invalidate();resolve('A');await pending;assert.equal(apply,'B');
 let reject!:(e:Error)=>void;const failed=session.receive(session.begin(),()=>new Promise<string>((_,r)=>reject=r),v=>apply=v,()=>apply='old error');session.invalidate();reject(new Error('late'));await failed;assert.equal(apply,'B');
});
test('historical failed children remain explicit and display geometry preserves extremes',()=>{
 const rows=selectionOptions({uuid:'run',children:[{uuid:'job1',execution_id:'exec',attempt:1},{uuid:'job2',execution_id:'exec',attempt:2}]});assert.equal(rows.length,2);
 assert.deepEqual(cellEdges([0,2,7]),[-1,1,4.5,9.5]);const scale=displayScale([-1e308,1e308]);assert.equal(scale.position(-1e308),0);assert.equal(scale.position(1e308),1);assert.throws(()=>cellEdges([-1e308,1e308]));
});

test('readback and historical binding integrity retain distinct unequal scopes',()=>{const {response,context}=nativeFixture();const value=readNativeEnvelope(response,context,'manifest');assert.equal(value.retained.integrity.readback.native_contexts,'not_reverified');assert.equal(value.retained.integrity.original_binding.native_contexts,'verified');for(const integrity of [{...response.retained.integrity,readback:response.retained.integrity.original_binding},{...response.retained.integrity,original_binding:null},{...response.retained.integrity,readback:{...response.retained.integrity.readback,current_access:'verified'}}])assert.throws(()=>readNativeEnvelope({...response,retained:{...response.retained,integrity}},context,'manifest'));});

export function pointFixture(){const f=nativeFixture();return {...f,metadata:{schema:'qcl-negf.portal-point-source.v1',status:'available',scope:'original_owner_metadata',provider_selection:f.selection,point:{point_id:'point',point_attempt:2,plan_point_id:'point'},source:{kind:'final',relation:'current_point_attempt',reason:null},source_state:f.state,retained:{...f.response.retained,creator:{calcjob_uuid:f.selection.calcjob_uuid,binding_uuid:null}}}};}
test('point metadata obtains original full context without manual pins',async()=>{
 const data=await import('../src/saved-results-data.ts');const f=pointFixture();
 const value=data.readPointSource(f.metadata,f.selection,'point');const context=data.pointContext(value)!;
 assert.deepEqual(context,{...f.context,state_ref:f.state});
 assert.deepEqual(data.pointRequest(f.selection,'point'),{execution_id:'exec',attempt:2,calcjob_uuid:f.selection.calcjob_uuid,point_id:'point'});
 assert.throws(()=>readManifest({...f.response,source_state:{...f.state,state_id:'different'}},context));
});

test('strict point projection distinguishes absent, checkpoint and retained original creator',async()=>{
 const data=await import('../src/saved-results-data.ts');const f=pointFixture();
 const absent={...f.metadata,source:{kind:'absent',relation:null,reason:'not_recorded'},source_state:null,retained:{...f.metadata.retained,creator:null}};
 assert.equal(data.pointContext(data.readPointSource(absent,f.selection,'point')),null);
 const checkpoint={...f.metadata,source:{kind:'checkpoint',relation:'current_point_attempt',reason:'interrupted'}};
 assert.equal(data.readPointSource(checkpoint,f.selection,'point').source.kind,'checkpoint');
 const retained={...f.metadata,source:{kind:'final',relation:'retained_source',reason:'failed_new_attempt'},retained:{...f.metadata.retained,creator:{calcjob_uuid:'55555555-5555-4555-8555-555555555555',binding_uuid:'77777777-7777-4777-8777-777777777777'}}};
 assert.deepEqual(data.readPointSource(retained,f.selection,'point').retained.creator,retained.retained.creator);
 for(const bad of [{...f.metadata,access:{}},{...f.metadata,scope:'current_ro'},
  {...f.metadata,provider_selection:{...f.selection,run_uuid:f.selection.calcjob_uuid}},
  {...f.metadata,point:{...f.metadata.point,plan_point_id:'foreign'}},
  {...f.metadata,point:{...f.metadata.point,point_attempt:9007199254740992}},
  {...f.metadata,point:{...f.metadata.point,point_attempt:true}},
  {...f.metadata,source:{...f.metadata.source,artifact_root:'/private'}},
  {...f.metadata,source:{kind:'final',relation:'current_point_attempt',reason:'guessed'}},
  {...absent,source_state:f.state},
  {...f.metadata,source_state:{...f.state,state_sequence:'0'}},
  {...f.metadata,source_state:{...f.state,manifest:{...f.state.manifest,path:'/private'}}},
  {...f.metadata,source_state:{...f.state,manifest:{...f.state.manifest,bytes:'18446744073709551616'}}},
  {...f.metadata,retained:{...f.metadata.retained,creator:null}},
  {...f.metadata,retained:{...f.metadata.retained,native_binding:{...f.pin,extra:'untrusted'}}},
  {...f.metadata,retained:{...f.metadata.retained,integrity:{...f.metadata.retained.integrity,readback:f.metadata.retained.integrity.original_binding}}},
  {...f.metadata,retained:{...f.metadata.retained,creator:{calcjob_uuid:'55555555-5555-4555-8555-555555555555',binding_uuid:null}}},
 ])assert.throws(()=>data.readPointSource(bad,f.selection,'point'));
 assert.throws(()=>data.readPointSource({...f.metadata,extra:'x'.repeat(2097152)},f.selection,'point'));
 assert.throws(()=>data.pointRequest({...f.selection,attempt:9007199254740992},'point'));
});
test('same-point explicit refresh rejects out-of-order success and error and old full StateRef',async()=>{
 const data=await import('../src/saved-results-data.ts');const f=pointFixture(),session=new SavedSession();let applied='new',late!:(v:unknown)=>void;
 const old=session.receive(session.begin(),()=>new Promise(r=>late=r),()=>applied='old success',()=>applied='old error');
 const fresh=session.begin();late(f.metadata);await old;assert.equal(applied,'new');assert.ok(session.current(fresh));
 let reject!:(e:unknown)=>void;const error=session.receive(session.begin(),()=>new Promise((_,r)=>reject=r),()=>applied='old success',()=>applied='old error');session.begin();reject(new Error('old'));await error;assert.equal(applied,'new');
 const renewed=data.readPointSource({...f.metadata,source_state:{...f.state,state_id:'new',manifest:{...f.state.manifest,sha256:'f'.repeat(64)}}},f.selection,'point');
 assert.throws(()=>readManifest(f.response,data.pointContext(renewed)!));
});
test('selected tuple and frozen point membership invalidates key without poll mtime churn',async()=>{
 const {pointChoice}=await import('../src/saved-results-data.ts');const f=pointFixture();
 const run={uuid:f.selection.run_uuid,children:[{uuid:f.selection.calcjob_uuid,execution_id:'exec',attempt:2}],plan_points:[{id:'point',execution_id:'exec',temperature_K:300,voltage_per_period_V:0}]};
 const a=pointChoice('token',run as never,f.selection.calcjob_uuid,'point');assert.ok(a.selection&&a.point);
 assert.equal(a.key,pointChoice('token',{...run,mtime:'later'} as never,f.selection.calcjob_uuid,'point').key);
 for(const next of [{...run,children:[]},{...run,plan_points:[]},{...run,children:[{...run.children[0],attempt:3}]},{...run,uuid:f.selection.calcjob_uuid}])assert.notEqual(a.key,pointChoice('token',next as never,f.selection.calcjob_uuid,'point').key);
 assert.notEqual(a.key,pointChoice('new token',run as never,f.selection.calcjob_uuid,'point').key);
 assert.equal(pointChoice('token',{...run,plan_points:[...run.plan_points,...run.plan_points]} as never,f.selection.calcjob_uuid,'point').point,undefined);
});

test('metadata scalar spelling rejects newline aliases and malformed relative references',async()=>{
 const data=await import('../src/saved-results-data.ts');const f=pointFixture();
 assert.throws(()=>data.pointRequest(f.selection,'point\n'));
 for(const pin of [{...f.pin,node_uuid:f.pin.node_uuid+'\n'},{...f.pin,sha256:f.pin.sha256+'\n'},{...f.pin,bytes:'2\n'}])assert.throws(()=>data.readPointSource({...f.metadata,retained:{...f.metadata.retained,native_binding:pin}},f.selection,'point'));
 for(const path of ['../private','a//state.json','a/./state.json','a\\state.json',''])assert.throws(()=>data.readPointSource({...f.metadata,source_state:{...f.state,manifest:{...f.state.manifest,path}}},f.selection,'point'));
});
