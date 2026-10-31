import assert from 'node:assert/strict';
import {test} from 'node:test';
import {checkedLive} from '../src/live-performance-data.ts';
import {SavedSession} from '../src/saved-results-data.ts';
const exact={run_uuid:'run',calcjob_uuid:'job',execution_id:'ex',attempt:7};
const request={mode:'retained',anchor_key:'a'.repeat(64)};
const state={identity:{attempt:'3'},manifest:{sha256:'b'.repeat(64)}};
const entry={anchor_key:request.anchor_key,telemetry:{expected_transport:{selection:{calcjob_uuid:'job',execution_id:'ex',attempt:7}}},runner_anchor_utf8:JSON.stringify({scientific:{selected_state:state}})};
const runtime={scope:'aiida_public_source_getters',status:'not_measured',actual_io_bytes:null,heap_bytes:null,peak_rss_bytes:null,reason:'not_reported_by_public_source_getters'};
const available={schema:'qcl-negf.portal-performance.v2',operation:'performance',status:'available',provider_selection:exact,...request,
 source_metadata:{schema:'qcl-negf.performance-source.v2',status:'available',provider_selection:exact,anchor_key:request.anchor_key,origin:{mode:'retained'},entry,index:{raw_utf8:JSON.stringify({entries:[entry]})}},
 access:{status:'checked_ro_current_request',method:'local_binding_mountinfo_nofollow_domain',config_sha256:'c'.repeat(64),scope:'selected_scientific_and_technical_roots'},
 view:{schema:'qcl-negf.performance-summary-view.v2',status:'available',scope:'execution_attempt',source_state:state,transport:entry.telemetry.expected_transport,
 summary:{max_observed_rss_bytes:null},io_counters:{bytes:'18446744073709551615'},observer_self_cost:{cpu_seconds:null}},owner_runtime:runtime};
test('v2 whole envelope preserves borrowed StateRef and exact unknown metrics',()=>{
 assert.equal(checkedLive(available,exact,'performance',request),available);
 assert.equal(available.view.io_counters.bytes,'18446744073709551615');
 for(const bad of [{...available,anchor_key:'f'.repeat(64)},{...available,mode:'current'},
 {...available,view:{...available.view,source_state:{...state,identity:{attempt:'7'}}}},
 {...available,source_metadata:{...available.source_metadata,index:{raw_utf8:'{"entries":[]}'}}},
 {...available,source_metadata:{...available.source_metadata,entry:{...entry,anchor_key:'f'.repeat(64)},index:{raw_utf8:JSON.stringify({entries:[{...entry,anchor_key:'f'.repeat(64)}]})}}},
 {...available,owner_runtime:{...runtime,actual_io_bytes:0}},{...available,costs:{}},
 {...available,view:{...available.view,scope:'point'}}])assert.throws(()=>checkedLive(bad,exact,'performance',request));
});
test('staging/unavailable preserve owning reason and forbid partial views',()=>{
 for(const status of ['staging','unavailable']){
 const data={...available,status,source_metadata:{schema:'qcl-negf.performance-source.v2',provider_selection:exact,status,reason:'scientific_anchor_pending',candidates:[]},access:null,view:null};
 assert.equal(checkedLive(data,exact,'performance',request),data);
 assert.throws(()=>checkedLive({...data,view:available.view},exact,'performance',request));
 }
});
test('context and cancellation invalidate late performance success and error',async()=>{
 const s=new SavedSession();let success=0,error=0;const g=s.begin();s.invalidate();
 await s.receive(g,async()=>available,()=>success++,()=>error++);
 await s.receive(g,async()=>{throw Error('late');},()=>success++,()=>error++);
 assert.equal(success,0);assert.equal(error,0);
});
test('live source legacy branch remains independently validated',()=>{
 const prefix={provider_selection:exact,scope:'closed_prefix',descriptor_verified:true,index_sha256:'a'.repeat(64),generation:1,cutoff:{scba_rows:2,outer_rows:0}};
 assert.equal(checkedLive(prefix,exact,'live-source'),prefix);
 assert.throws(()=>checkedLive({...prefix,descriptor_verified:false},exact,'live-source'));
});
