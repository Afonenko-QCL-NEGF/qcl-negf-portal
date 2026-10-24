import assert from 'node:assert/strict';
import { test } from 'node:test';
import { getModelHelp, readUnknownModelHelp } from '../src/api.ts';
import { checkedBundle, checkedFragment, docHref, RequestEpoch } from '../src/model-help-data.ts';

const context = {run_uuid:'e7f5313d-22fd-49fa-9be5-85fbe29e1974', calcjob_uuid:'10dd2a4c-031b-425c-90b8-2f7ea564d313', execution_id:'execution-1', point_id:'point-1', attempt:2, plan_fingerprint:'a'.repeat(64), scientific_fingerprint:'b'.repeat(64), configuration_hash:'c'.repeat(64), model_revision:'model-A'};
const view = {format:'qcl-negf.model-help-view.v1', context, binding:{status:'unknown', reason:'source_binding_missing', release_id:'A', source_revision:null, bundle_sha256:null, release_manifest_sha256:null}, fragment:null, inputs:[], input_status:{status:'unavailable',reason:'inventory_unavailable'}};

test('exact selectors use one header-only no-store GET; unknown does not fetch fragments', async () => {
  const original = globalThis.fetch; let calls = 0;
  globalThis.fetch = async (url, init) => {
    calls++;
    assert.equal(String(url), `/api/v1/runs/${context.run_uuid}/model-help?fragment_id=scba&execution_id=execution-1&point_id=point-1&attempt=2&calcjob_uuid=${context.calcjob_uuid}`);
    assert.equal(init?.method, 'GET'); assert.equal(init?.cache, 'no-store');
    assert.deepEqual(init?.headers, {Authorization:'Bearer private-token'});
    return new Response(JSON.stringify(view));
  };
  try {assert.deepEqual(await getModelHelp('private-token',context.run_uuid,'scba',{execution_id:context.execution_id, point_id:context.point_id,attempt:2,calcjob_uuid:context.calcjob_uuid}),view); assert.equal(calls,1);}
  finally {globalThis.fetch=original;}
});
test('bound, scalar inputs and malformed contexts are unsupported', () => {
  for (const bad of [{...view,binding:{...view.binding,status:'bound'}},{...view,inputs:[{}]},{...view,context:{...context,attempt:0}}]) assert.throws(()=>readUnknownModelHelp(bad));
});
test('late responses cannot restore run A after B or logout', async () => {
  const epoch = new RequestEpoch(); const a = epoch.next();
  let resolve!: (value: string) => void;
  const pending = new Promise<string>(done=>{resolve=done;});
  const response = pending.then(v=>epoch.current(a)?v:null);
  epoch.next(); resolve('A'); assert.equal(await response,null);
  const b = epoch.next(); epoch.invalidate(); assert.equal(epoch.current(b),false);
});
const rev='b'.repeat(40);
test('exact-version safe DocRef refuses escapes and foreign paths', () => {
  assert.equal(docHref(rev,{section:'core',path:'theory/a.html',anchor:'equation-1'}),`/docs/${rev}/core/theory/a.html#equation-1`);
  for(const path of ['../a.html','core/%2e%2e/a.html','https://bad/a.html','/core/a.html','core/../a.html']) assert.throws(()=>docHref(rev,{section:'core',path,anchor:null}));
});
test('G1 bundle and fragment identities fail closed; empty descriptors stay empty', () => {
  assert.deepEqual(checkedBundle({format:'qcl-negf.docs-bundle.v1',sources:{revision:rev},fragments:[]},rev),[]);
  assert.throws(()=>checkedBundle({format:'qcl-negf.docs-bundle.v1',sources:{revision:'a'.repeat(40)},fragments:[]},rev));
  const fragment={format:'qcl-negf.model-help-fragment.v1',id:'scba',source_revision:rev,title:'<script>text</script>',summary:'summary',scope:'baseline',full:{section:'core',path:'a.html',anchor:null},meaning_source:{component:'root',revision:rev,path:'x',selector:{kind:'file',value:''},sha256:'a'.repeat(64)},related:[],equations:[],inputs:[],api:[],assumptions:[],limitations:[]};
  assert.equal(checkedFragment(fragment,rev,'scba').title,fragment.title);
  assert.throws(()=>checkedFragment({...fragment,id:'other'},rev,'scba'));
  assert.throws(()=>checkedFragment({...fragment,full:{...fragment.full,path:'core/%2e.html'}},rev,'scba'));
});

test('static fetch verifies exact bytes, SHA and bounded response; no bearer', async () => {
  const {checkedJson}=await import('../src/model-help-data.ts');
  const original=globalThis.fetch; const raw=new TextEncoder().encode('{"fragments":[]}');
  const sha=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',raw)),b=>b.toString(16).padStart(2,'0')).join('');
  globalThis.fetch=async(url,init)=>{assert.equal(String(url),`/docs/${rev}/bundle.json`);assert.equal(init?.headers,undefined);assert.equal(init?.redirect,'error');return new Response(raw);};
  try {
    assert.deepEqual(await checkedJson(`/docs/${rev}/bundle.json`,200,sha),{fragments:[]});
    await assert.rejects(()=>checkedJson(`/docs/${rev}/bundle.json`,1,sha));
    await assert.rejects(()=>checkedJson(`/docs/${rev}/bundle.json`,200,'0'.repeat(64)));
    await assert.rejects(()=>checkedJson('/docs/current/bundle.json',200,sha));
  } finally {globalThis.fetch=original;}
});
test('503 read never automatically retries',async()=>{
  const original=globalThis.fetch; let calls=0;
  globalThis.fetch=async()=>{calls++;return new Response('{"detail":"offline"}',{status:503});};
  try {await assert.rejects(()=>getModelHelp('token',context.run_uuid,'scba',{execution_id:context.execution_id,point_id:context.point_id}));assert.equal(calls,1);}
  finally {globalThis.fetch=original;}
});
test('final newline never extends fragment/point or UUID grammar',async()=>{
  await assert.rejects(()=>getModelHelp('token',context.run_uuid,'scba\n',{execution_id:context.execution_id,point_id:context.point_id}));
  await assert.rejects(()=>getModelHelp('token',context.run_uuid,'scba',{execution_id:context.execution_id,point_id:context.point_id+'\n'}));
});
