import assert from 'node:assert/strict';
import {test} from 'node:test';
import {cellEdges,displayScale,readManifest,readSlice,readNativeEnvelope,nativePin,SavedSession,selectionOptions} from '../src/saved-results-data.ts';
import type {NativeContext,StateRef} from '../src/saved-results-data.ts';
import {readSources,readBrief,ieeeCell,declaredText,catalogChoice} from '../src/saved-results-data.ts';
import type {SourcesPage,BriefPage} from '../src/saved-results-data.ts';

export function catalogFixture(){
 const f=nativeFixture(),observations={path:'observations.h5',bytes:'100',sha256:'e'.repeat(64),schema:'qcl-negf-physics-analysis-v5',media_type:'application/x-hdf5'};
 const retained={native_result:f.pin,native_binding:f.pin,input_plan:f.pin,source_graph:f.pin,integrity:f.response.retained.integrity};
 const row={point_id:'point',declared_coordinates:{temperature_K:{kind:'json_integer',decimal:'1'+'0'.repeat(2000)},voltage_per_period_V:{kind:'ieee',type:'f64',bits:'8000000000000000'},branch:'reverse',order:'1'+'0'.repeat(2000)},row_status:'recorded',point_attempt:'9',source:{kind:'final',relation:'current_point_attempt',reason:null},source_state:f.state,source_invocation:{source_epoch:'epoch'},observations_ref:observations,creator:{calcjob_uuid:f.selection.calcjob_uuid,binding_uuid:null},recorded_statuses:{status:'failed',quality:'invalid',converged:false},missing_reason:null};
 const ordered=(v:unknown):unknown=>Array.isArray(v)?v.map(ordered):v&&typeof v==='object'?Object.fromEntries(Object.entries(v).sort(([a],[b])=>a.localeCompare(b,'en')).map(([k,x])=>[k,ordered(x)])):v;
 const token=Buffer.from(JSON.stringify(ordered({schema:'qcl-negf.portal-native-page.v1',provider_selection:f.selection,...Object.fromEntries(['native_result','native_binding','input_plan','source_graph'].map(k=>[k,f.pin])),offset:'0',limit:8}))).toString('base64url');
 const page={schema:'qcl-negf.portal-native-sources.v1',scope:'original_owner_metadata',provider_selection:f.selection,retained,pagination:{offset:'0',limit:8,total:'1',page_ref:token,previous_cursor:null,next_cursor:null},points:[row]} as unknown as SourcesPage;
 const counter={bytes_read_total:10,sha_bytes:0,hdf_bytes:0,metadata_bytes:10,logical_selected_bytes:0,maximum_io_bytes:67108864};
 const view={source_state:f.state,identity:f.state.identity,invocation:row.source_invocation,artifact_ref:observations,request:{coordinate_checks:{voltage:{actual:{kind:'ieee',type:'f64',bits:'3fa999999999999a'}},temperature:{actual:{kind:'ieee',type:'f64',bits:'4069000000000000'}}}},scalar_observables:{current_density_A_per_m2:{kind:'ieee',type:'f64',bits:'8000000000000000'},sheet_density_per_m2:null},summary:{scientific_accepted:null,discretization_verified:'not_measured',status:'invalid'},issues:[]};
 const brief={...page,schema:'qcl-negf.portal-native-brief.v1',points:[{...row,read:{status:'available',reason:null},access:f.response.access,view}],credits:{final_sources:1,stage_share:67108864,reserved_bytes:134217728,actual_delivered_bytes:20,epochs:[{point_id:'point',initialization:counter,brief_and_close:counter}]}} as unknown as BriefPage;
 return {page,brief,f};
}
test('R1 exact finite page keeps 2001-digit declarations and signed IEEE before native brief',()=>{
 const {page,brief,f}=catalogFixture();const p=readSources(page,f.selection);assert.equal(declaredText(p.points[0].declared_coordinates.temperature_K),'1'+'0'.repeat(2000));assert.equal(p.points[0].declared_coordinates.order.length,2001);
 const b=readBrief(brief,p);assert.equal(ieeeCell(b.points[0].view!.scalar_observables.current_density_A_per_m2).text,'-0');
 assert.equal(ieeeCell(b.points[0].view!.request.coordinate_checks.voltage.actual).value,.05);
 for(const bad of [{...page,extra:true},{...page,points:[]},{...page,pagination:{...page.pagination,total:'01'}},{...page,pagination:{...page.pagination,page_ref:page.pagination.page_ref+'='}}])assert.throws(()=>readSources(bad,f.selection));
});
test('R1 full source, invocation, all five O1 fields and source order are pinned to metadata',()=>{
 const {page,brief}=catalogFixture();
 for(const key of ['path','bytes','sha256','schema','media_type']){const b=structuredClone(brief);(b.points[0].view!.artifact_ref as unknown as Record<string,unknown>)[key]='foreign';assert.throws(()=>readBrief(b,page),key);}
 for(const field of Object.keys(page.points[0].source_state!.identity)){const b=structuredClone(brief);(b.points[0].view!.identity as unknown as Record<string,unknown>)[field]='foreign';assert.throws(()=>readBrief(b,page),field);}
 for(const mutate of [(b:BriefPage)=>b.points[0].view!.invocation.source_epoch='foreign',(b:BriefPage)=>b.points[0].view!.source_state.state_id='foreign',(b:BriefPage)=>b.points[0].view!.source_state.state_sequence='2',(b:BriefPage)=>b.points[0].view!.source_state.manifest.bytes='101',(b:BriefPage)=>b.points[0].point_id='foreign']){const b=structuredClone(brief);mutate(b);assert.throws(()=>readBrief(b,page));}
});
test('R1 scalar codec retains raw finite extremes, zero, infinities, NaN payload and null',()=>{
 const values=[['0000000000000000','0'],['8000000000000000','-0'],['c008000000000000','-3'],['0000000000000001','5e-324'],['7fefffffffffffff','1.7976931348623157e+308'],['7ff0000000000000','positive_infinity'],['fff0000000000000','negative_infinity'],['7ff8000000000042','nan']];
 for(const [bits,text] of values){const raw={kind:'ieee',type:'f64',bits};const cell=ieeeCell(raw);assert.equal(cell.text,text);assert.deepEqual(cell.raw,raw);if(['nan','positive_infinity','negative_infinity'].includes(text))assert.equal(cell.value,null);}
 assert.equal(ieeeCell(null).reason,'missing_scalar');for(const bits of ['8000','7FF0000000000000','0000000000000000\n'])assert.throws(()=>ieeeCell({kind:'ieee',type:'f64',bits}));
});
test('R1 admitted native row u64 maximum stays a decimal string distinct from transport/StateRef counters',()=>{
 const {page,f}=catalogFixture();page.points[0].point_attempt='18446744073709551615';page.points[0].source_state!.identity.attempt='1';
 const read=readSources(page,f.selection);assert.equal(read.points[0].point_attempt,'18446744073709551615');assert.equal(read.points[0].source_state!.identity.attempt,'1');assert.equal(read.provider_selection.attempt,2);
});
test('R1 catalog choice ignores poll mtime and fences every selected tuple/removal change',()=>{
 const {f}=catalogFixture();const run={uuid:f.selection.run_uuid,children:[{uuid:f.selection.calcjob_uuid,execution_id:'exec',attempt:2}]} as never;
 const key=catalogChoice('token',run,f.selection.calcjob_uuid).key;assert.equal(catalogChoice('token',{...run as object,mtime:'poll'} as never,f.selection.calcjob_uuid).key,key);
 for(const [token,next] of [['new',run],['token',{...run as object,uuid:f.selection.calcjob_uuid}],['token',{...run as object,children:[]}],['token',{...run as object,children:[{uuid:f.selection.calcjob_uuid,execution_id:'other',attempt:2}]}],['token',{...run as object,children:[{uuid:f.selection.calcjob_uuid,execution_id:'exec',attempt:3}]}]] as const)assert.notEqual(catalogChoice(token,next as never,f.selection.calcjob_uuid).key,key);
});
test('R1 brief source order cannot be substituted by the same set of states',()=>{
 const {page,brief}=catalogFixture();const p2=structuredClone(page.points[0]);p2.point_id='point2';p2.source_state!.identity.point_id='point2';page.points.push(p2);page.pagination.total='2';
 const b2=structuredClone(brief.points[0]);Object.assign(b2,p2);b2.view!.identity.point_id='point2';b2.view!.source_state.identity.point_id='point2';brief.points.push(b2);brief.pagination=page.pagination;brief.points.reverse();assert.throws(()=>readBrief(brief,page));
});
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

export function historyFixture(){
 const {page,f}=catalogFixture();const row=page.points[0];
 const selected={...f.selection,point_id:row.point_id,provider:'native_nfs',retained:Object.fromEntries(['native_result','native_binding','input_plan','source_graph'].map(k=>[k,f.pin])),source_state:f.state,source_invocation:{source_epoch:'epoch'},reduction:{stage:'scba',field:'J',first_record:'2',last_record:'6'}};
 const counter={bytes_read_total:10,sha_bytes:0,hdf_bytes:0,metadata_bytes:10,logical_selected_bytes:0,maximum_io_bytes:44739242};
 const credits={source_io_total:134217728,epochs_per_source:3,source_count:1,stage_share:44739242,reserved_bytes:134217726,actual_delivered_bytes:30,epochs:['construction','import','derive_and_close'].map(phase=>({source:0,phase,counters:counter})),metadata_readback_reserved:{owners:1,metadata_bytes:335544320,logical_io_bytes:268435456},metadata_readback_actual:null,metadata_readback_actual_reason:'not_returned_by_public_owner_API',copy_workspace:{raw_result_upper_bound:20000000,copies_and_encoding:3,reserved_bytes:60065536,maximum_bytes:134217728,nonrefundable:true,basis:'two_catalog_locators_plus_record_unit_and_exact_source_wrappers'},scope:'logical_source_metadata_copy_credit_not_RSS'};
 const cutoff={head:{path:'catalog.json',bytes:'20',sha256:'a'.repeat(64),schema:'qcl-negf.scientific-catalog.v1',media_type:'application/json'},entries:'1',records:'6',scba_rows:'5',outer_rows:'0',witness_updates:'1',last_record:'6'};
 const locator={source_identity:f.state.identity,source_epoch:'epoch',append_ordinal:'4',leaf:{path:'rows.h5',bytes:'100',sha256:'e'.repeat(64),schema:'qcl-negf-scientific-history-v5',media_type:'application/x-hdf5'},row_offset:'3'};
 const result={source:f.state,request:selected.reduction,units:'A/m^2',observations:'4',updates:'1',finite_count:'2',nonfinite_count:'2',missing_count:'0',minimum:{value:{kind:'ieee',type:'f64',bits:'c010000000000000'},locator:{...locator,append_ordinal:'2',row_offset:'1'}},maximum:{value:{kind:'ieee',type:'f64',bits:'8000000000000000'},locator},coverage:{first_record:'2',last_record:'6',witness_updates:'counted_separately_no_scalar_replacement'},io_counters:counter,derived_by:'qcl_negf_results.live_views.derive_history.v1'};
 const response={...f.response,operation:'derive-history',retained:{...f.response.retained,creator:row.creator},view:{result,source_invocation:selected.source_invocation,history_cutoff:cutoff,source_status:{process:{state:'failed',exit_code:1,reason:'NEW fixture'},...Object.fromEntries(['convergence','physics','discretization','experimental'].map(k=>[k,{state:'not_measured',reason:'No science evidence',evidence:null}]))},source_closed:true,input_plan:f.pin,source_graph:f.pin,credits,scientific_acceptance:'not_assessed'}};
 return {selected,response,result,page,credits};
}
test('P10 history response preserves exact signed IEEE, strings, units and full source receipts',async()=>{
 const d=await import('../src/saved-results-data.ts');const {selected,response}=historyFixture();
 const read=d.readHistoryDerive(response,selected as never);assert.equal(read.view.result.maximum.value.bits,'8000000000000000');
 for(const mutate of [(r:any)=>r.view.result.source.identity.attempt='1',(r:any)=>r.view.result.maximum.locator.source_epoch='foreign',(r:any)=>r.view.result.observations=4,(r:any)=>r.view.source_invocation.source_epoch='foreign',(r:any)=>r.view.input_plan.sha256='0'.repeat(64),(r:any)=>r.view.result.request.last_record='7',(r:any)=>r.view.credits.epochs.pop(),(r:any)=>r.retained.creator=null,(r:any)=>r.view.source_status.process.exit_code=9007199254740992]){const r=structuredClone(response);mutate(r);assert.throws(()=>d.readHistoryDerive(r,selected as never));}
});
test('P10 explicit history selection requires all four genuine pins and u64 range',async()=>{
 const d=await import('../src/saved-results-data.ts');const {page}=historyFixture();
 const selected=d.historySelection(page,page.points[0],{stage:'scba',field:'J',first_record:'1',last_record:'18446744073709551615'});
 assert.equal(selected.reduction.last_record,'18446744073709551615');assert.equal(selected.source_invocation.source_epoch,'epoch');
 assert.throws(()=>d.historySelection(page,page.points[0],{stage:'scba',field:'J',first_record:'0',last_record:'2'}));
});

test('P10 action session synchronously excludes a duplicate and fences late result/error on every edit',async()=>{
 const d=await import('../src/saved-results-data.ts');const session=new d.HistorySession();let shown='unchanged';
 const first=session.beginAction();assert.notEqual(first,null);assert.equal(session.beginAction(),null);
 let resolve!:(v:string)=>void;const pending=session.receiveAction(first!,()=>new Promise<string>(r=>resolve=r),v=>shown=String(v),()=>shown='late error');session.invalidate();resolve('late success');await pending;assert.equal(shown,'unchanged');
 let reject!:(e:Error)=>void;const second=session.beginAction()!;const failure=session.receiveAction(second,()=>new Promise<string>((_,r)=>reject=r),v=>shown=String(v),()=>shown='late error');session.invalidate();reject(new Error('late'));await failure;assert.equal(shown,'unchanged');assert.notEqual(session.beginAction(),null);
});
