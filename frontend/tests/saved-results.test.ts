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
