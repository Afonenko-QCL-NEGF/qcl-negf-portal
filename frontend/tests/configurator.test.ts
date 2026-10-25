import assert from 'node:assert/strict';
import {test} from 'node:test';
import {api, ApiError} from '../src/api.ts';
import {PreviewSession, SubmissionGate, readCatalog, readPreview, makeNumericalVariant, makeRequest, verifyPlan, mutationUncertain} from '../src/configurator-data.ts';
const hash='a'.repeat(64);
const template={id:'equilibrium-0-70',name:'Candidate',study:'config/studies/equilibrium-0-70.yaml',study_sha256:hash,description:'Not SCI4 acceptance',model:{name:'GaAs/AlGaAs',path:'config/model/reference-2019-70k.yaml',sha256:hash},protocol:{name:'Analytical diagnostic',path:'config/policies/analytical-diagnostic.yaml',version:'research-source:config/policies/analytical-diagnostic.yaml',sha256:hash}};
const catalog={status:'available',catalog_source_id:'research-source',templates:[template]};
const raw=' {"schema":"qcl-negf-scientific-plan-v2","large":10000000000000000001,"tiny":1.230000000000000001e-30}\n';
async function fixture() {return {status:'available',draft_sha256:hash,semantic_draft_sha256:hash,catalog_source_id:'research-source',template,draft_yaml:'kind: study\n',short_form:{lossless:false,reason:'authoritative_yaml_requires_preservation'},variant_diffs:[],resolved_inputs:[{execution_id:'e1',configuration:{physical:{temperature_K:70}},assigning_sources:{}}],effective_defaults:[{execution_id:'e1',sections:{physical:{used:{T:{value:70,unit:'K'},optional:null},origin:'owning_resolution_rule',rule_source:{sha256:hash,symbol:'loader'},input_assigning_histories:{}}},point_sections:[{point_id:'p1',sections:{physical:{used:{T:{value:70,unit:'K'}}}}}]}],default_rule_sources:{runner:{sha256:hash},core:[{sha256:hash}]},plan:raw,plan_sha256:await verifyPlan(raw),fingerprint:hash,scientific_fingerprint:hash,counts:{points:1,executions:1},estimates:{estimated_peak_bytes:4096,minimum_output_bytes:256}};}
export {fixture,catalog};

test('finite form has no JS scientific defaults; original YAML bytes remain exact',()=>{
  assert.deepEqual(makeRequest('form','equilibrium-0-70','research-source',{},''),{mode:'form',template_id:'equilibrium-0-70',catalog_source_id:'research-source',fields:{}});
  const yaml='# Expert comment\nconfiguration:\n  overrides:\n    solver:\n      tolerance: 1e-12\n';
  assert.equal(makeRequest('yaml','equilibrium-0-70','research-source',{temperature_K:NaN},yaml).yaml,yaml);
  assert.throws(()=>makeRequest('form','x','s',{temperature_K:NaN},''));
});
test('production DTO retains exact plan, additive constructor sources and point projections',async()=>{
  assert.deepEqual(readCatalog(catalog),catalog);const value=await fixture();
  assert.deepEqual(readPreview(value,'research-source','equilibrium-0-70'),value);
  for(const change of [{plan_sha256:'wrong'},{catalog_source_id:'foreign'},{template:{...template,id:'foreign'}},{short_form:{}},{counts:{points:Infinity,executions:1}},{effective_defaults:null}])assert.throws(()=>readPreview({...value,...change},'research-source','equilibrium-0-70'));
  assert.equal(await verifyPlan(value.plan,value.plan_sha256),value.plan_sha256);
  await assert.rejects(verifyPlan(value.plan+' ',value.plan_sha256),/SHA/);
});
test('production async admission ignores late success and error after edit/context change',async()=>{
  const session=new PreviewSession();let release!:(v:unknown)=>void;let displayed='';
  const pending=new Promise<unknown>(ok=>{release=ok;});
  const a=session.begin();const old=session.receive(a,()=>pending,v=>{displayed=String(v);},e=>{displayed=String(e);});
  session.invalidate();const b=session.begin();await session.receive(b,async()=> 'B',v=>{displayed=String(v);},()=>{});release('A');await old;
  assert.equal(displayed,'B');assert.equal(session.current(a),false);
  let reject!:(e:Error)=>void;const c=session.begin();const error=session.receive(c,()=>new Promise((_,no)=>{reject=no;}),()=>{},e=>{displayed=String(e);});
  session.invalidate();reject(new Error('stale error'));await error;assert.equal(displayed,'B');
});
test('same-turn submission latch, invalidation while hashing and sticky unknown fence',async()=>{
  const gate=new SubmissionGate();const selected={plan:raw,sha256:await verifyPlan(raw)};
  gate.select(selected);assert.equal(gate.begin(),selected);assert.equal(gate.begin(),null);
  gate.select(null);assert.equal(gate.current(selected),false);gate.finish(false);
  gate.select(selected);assert.equal(gate.begin(),selected);gate.finish(true);gate.select(null);gate.select(selected);assert.equal(gate.begin(),null);
});
test('one authenticated preview POST and one exact existing submit POST; unknown has diagnostics, no retry',async()=>{
  const original=globalThis.fetch,calls:[string,RequestInit|undefined][]=[];const value=await fixture();
  globalThis.fetch=async(url,init)=>{calls.push([String(url),init]);return String(url).endsWith('/preview')?new Response(JSON.stringify(value)):new Response('{"detail":"Unknown outcome"}',{status:503,headers:{'X-QCL-Operation-ID':'operation-1','X-QCL-Operation-Outcome':'unknown'}});};
  try {
    const preview=readPreview(await api('token','/configurator/preview',makeRequest('form',template.id,'research-source',{},'')),'research-source',template.id);
    await assert.rejects(api('token','/runs',{plan:preview.plan,code_uuid:'code',resources:{},label:''}),err=>{assert.ok(err instanceof ApiError);assert.equal(err.operationId,'operation-1');assert.equal(mutationUncertain(err),true);return true;});
    assert.equal(calls.length,2);assert.equal(JSON.parse(String(calls[1][1]?.body)).plan,raw);
    assert.ok(calls.every(([,init])=>(init?.headers as Record<string,string>).Authorization==='Bearer token'&&init?.cache==='no-store'));
    assert.equal(mutationUncertain(new ApiError('rejected',new Headers({'X-QCL-Operation-Outcome':'rejected'}),422)),false);
    assert.equal(mutationUncertain(new Error('network disconnected')),true);
  } finally {globalThis.fetch=original;}
});

test('2^53 boundary and Intmax remain exact tagged decimal text, including read-only frozen topology',async()=>{
  const value:Record<string,unknown>=await fixture();
  const tagged={kind:'integer',decimal:'9223372036854775807'};
  value.estimates={estimated_peak_bytes:4096,minimum_output_bytes:256,boundary:9007199254740991,budget:tagged};
  const accepted=readPreview(value,'research-source','equilibrium-0-70');
  assert.deepEqual(accepted.estimates,value.estimates);
  assert.throws(()=>readPreview({...value,estimates:{budget:9007199254740992}},'research-source','equilibrium-0-70'));
  assert.equal(value.plan,raw);

});

test('explicit count controls generate only existing grammar numerical overrides; no defaults or silent rounding',()=>{
  assert.deepEqual(makeNumericalVariant('grid','discretization','numerical.energy_nodes',{energy_nodes:'101',momentum_nodes:''}),{id:'grid',comparison_kind:'discretization',controlled_paths:['numerical.energy_nodes'],overrides:{numerical:{energy_nodes:101}}});
  assert.equal(makeNumericalVariant('grid','discretization','numerical.energy_nodes',{energy_nodes:'9007199254740991'}).overrides.numerical.energy_nodes,9007199254740991);
  const rejected=['1.0000000000000001','9007199254740991.1','1.5','9007199254740992','NaN','Infinity','01','+1','1e2',' 101','101 ','101\n','0','-1','0x65'];
  for(const raw of rejected) {
    const draft={energy_nodes:raw};
    assert.throws(()=>makeNumericalVariant('grid','discretization','numerical.energy_nodes',draft));
    assert.equal(draft.energy_nodes,raw,'Rejected original draft text remains untouched');
  }
  assert.throws(()=>makeNumericalVariant('grid','discretization','numerical.energy_nodes',{}));
});
