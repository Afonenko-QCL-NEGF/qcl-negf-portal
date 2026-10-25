import assert from 'node:assert/strict';
import {test} from 'node:test';
import {checkedLive} from '../src/live-performance-data.ts';
const exact={run_uuid:'run',calcjob_uuid:'job',execution_id:'ex',attempt:2};
test('performance preserves unknown capacity and borrowed-source separation',()=>{
 const data={provider_selection:exact,status:'available',schema:'qcl-negf.performance-summary-view.v1',scope:'execution_attempt',closed:true,binding:{selection:exact},summary:{cpu_coverage:{cpu_seconds:3,covered_capacity:null,reason:'allocation_width_unknown'},max_observed_rss_bytes:null}};
 assert.equal(checkedLive(data,exact,'performance'),data);
 assert.equal((data.summary.cpu_coverage).covered_capacity,null);
 assert.throws(()=>checkedLive({...data,binding:{selection:{...exact,attempt:1}}},exact,'performance'));
 assert.throws(()=>checkedLive({...data,scope:'point'},exact,'performance'));
 const absent={provider_selection:exact,status:'unavailable',reason:'performance_binding_missing'};
 assert.equal(checkedLive(absent,exact,'performance').reason,'performance_binding_missing');
});
test('graph-only mapping is not verified live data; foreign selections rejected',()=>{
 const prefix={provider_selection:exact,scope:'closed_prefix',descriptor_verified:true,index_sha256:'a'.repeat(64),generation:1,cutoff:{scba_rows:2,outer_rows:0}};
 assert.equal(checkedLive(prefix,exact,'live-source'),prefix);
 assert.throws(()=>checkedLive({...prefix,descriptor_verified:false},exact,'live-source'));
 assert.throws(()=>checkedLive({...prefix,provider_selection:{...exact,calcjob_uuid:'foreign'}},exact,'live-source'));
});
