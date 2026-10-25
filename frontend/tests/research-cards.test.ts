import assert from 'node:assert/strict';
import {test} from 'node:test';
import {readFile} from 'node:fs/promises';
import {ApiError, api, downloadResearchCard} from '../src/api.ts';
import {readCardVersion, readCardRun, readCardAnalysis, readCardRequest, readCard, selectionGeneration, lookupCardRequest} from '../src/research-card-data.ts';
const C='11111111-1111-4111-8111-111111111111', V='33333333-3333-4333-8333-333333333333';
const R='44444444-4444-4444-8444-444444444444', A='66666666-6666-4666-8666-666666666666';
const version={card_uuid:C,version_uuid:C,version:1,previous_version_uuid:null,title:'<script>bad</script>',bytes:128,sha256:'a'.repeat(64),ctime:'2026-10-25T00:00:00Z'};
const run={association_uuid:A,card_uuid:C,card_version_uuid:C,run_uuid:R,plan_fingerprint:'a'.repeat(64),root_kind:'study',root_definition_id:'D1',ctime:version.ctime};
const analysis={association_uuid:A,card_uuid:C,card_version_uuid:V,anchor_run_uuid:R,report_uuid:A,report_sha256:'b'.repeat(64),ctime:version.ctime};

test('literal version/native relation guards keep pinned identity and raw prose separate',()=>{
  assert.deepEqual(readCardVersion(version),version);assert.deepEqual(readCardRun(run),run);assert.deepEqual(readCardAnalysis(analysis),analysis);
  assert.deepEqual(readCardRequest({status:'stored',kind:'version',receipt:version}),{status:'stored',kind:'version',receipt:version});
  assert.deepEqual(readCardRequest({status:'not_found'}),{status:'not_found'});
  assert.equal(readCard({schema:'qcl-negf-research-card-v1',title:'Test',goal:' Compare A/B ',question:'Why?\nNegative results remain.'}).goal,' Compare A/B ');
  for(const value of [{...version,version:true},{...version,bytes:65537},{...version,version_uuid:V},
    {...version,extra:'invented'},{...version,previous_version_uuid:R},{...version,sha256:'wrong'},{...version,card_uuid:C+'\n'},
    {...version,version:2,previous_version_uuid:null}])assert.throws(()=>readCardVersion(value));
  assert.throws(()=>readCardRun({...run,plan_fingerprint:'wrong'}));
  assert.throws(()=>readCardAnalysis({...analysis,report_uuid:'inferred from question'}));
  assert.throws(()=>readCardRequest({status:'not_found',receipt:version}));
});

test('generation guard discards out-of-order A response after B selection',async()=>{
  const selection=selectionGeneration();const a=selection.next();let release!:()=>void;
  const pending=new Promise<void>(r=>{release=r;});let rendered='';
  const old=pending.then(()=>{if(selection.current(a))rendered='A';});
  const b=selection.next();if(selection.current(b))rendered='B';release();await old;
  assert.equal(rendered,'B');assert.equal(selection.current(a),false);
});

test('one explicit write retains client request and actor diagnostic headers; readback is GET',async()=>{
  const original=globalThis.fetch;const calls:[string,RequestInit|undefined][]=[];
  globalThis.fetch=async (url,init)=>{
    calls.push([String(url),init]);
    if(init?.method==='POST')return new Response(JSON.stringify({detail:'Mutation outcome is unknown'}),{status:503,headers:{
      'X-QCL-Operation-ID':A,'X-QCL-Operation-Outcome':'unknown','X-QCL-Unresolved-Operation-ID':R,
    }});
    return new Response(JSON.stringify({status:'not_found'}));
  };
  try {
    const raw=' {"schema":"qcl-negf-research-card-v1","title":"Test","goal":" Compare A/B ","question":"Why?\\nNegative results remain."}\n';
    await assert.rejects(api('secret','/research-cards',{request_uuid:C,card:raw}),err=>{
      assert.ok(err instanceof ApiError);assert.equal(err.operationId,A);assert.equal(err.outcome,'unknown');assert.equal(err.unresolvedOperationId,R);return true;
    });
    assert.deepEqual(await api('secret',`/research-card-requests/${C}`),{status:'not_found'});
    assert.equal(calls.length,2);assert.deepEqual(calls.map(([,i])=>i?.method),['POST','GET']);
    assert.equal(JSON.parse(String(calls[0][1]?.body)).card,raw);
    assert.ok(calls.every(([url,i])=>!url.includes('secret')&&i?.cache==='no-store'));
  } finally {globalThis.fetch=original;}
});

test('bounded card download retains exact bytes with bearer header and fixed filename',async()=>{
  const original=globalThis.fetch, originalDocument=Object.getOwnPropertyDescriptor(globalThis,'document');
  const create=URL.createObjectURL,revoke=URL.revokeObjectURL;const blobs:Blob[]=[];const clicks:string[]=[];
  const raw=' {"original":"bytes"}\n';
  globalThis.fetch=async(url,init)=>{assert.equal(String(url),`/api/v1/research-cards/${C}/versions/${V}/file`);
    assert.equal((init?.headers as Record<string,string>).Authorization,'Bearer secret');assert.equal(init?.cache,'no-store');return new Response(raw);};
  URL.createObjectURL=(blob)=>{blobs.push(blob as Blob);return 'blob:card';};URL.revokeObjectURL=url=>{assert.equal(url,'blob:card');};
  Object.defineProperty(globalThis,'document',{configurable:true,value:{createElement:()=>{const link={href:'',download:'',click:()=>clicks.push(link.download)};return link;}}});
  try {await downloadResearchCard('secret',C,V);assert.deepEqual(clicks,['research-card.json']);assert.equal(await blobs[0].text(),raw);
    globalThis.fetch=async()=>new Response('x'.repeat(65537));await assert.rejects(downloadResearchCard('secret',C,V),/byte limit/);assert.equal(clicks.length,1);
  } finally {globalThis.fetch=original;URL.createObjectURL=create;URL.revokeObjectURL=revoke;
    if(originalDocument)Object.defineProperty(globalThis,'document',originalDocument);else Reflect.deleteProperty(globalThis,'document');}
});

test('mount, explicit mutation, stale guards, pinned selectors and unknown readback controls are retained',async()=>{
  const source=await readFile(new URL('../src/research-cards.tsx',import.meta.url),'utf8');
  const main=await readFile(new URL('../src/main.tsx',import.meta.url),'utf8');
  assert.match(main,/<ResearchCards key=\{token\}/);assert.match(main,/<RunStructure key=\{detail.uuid\}/);
  for(const literal of ['generation.current.current(g)','reportGeneration.current.current(g)','runGeneration.current.current(g)',
    'base_version_uuid: version!.version_uuid','card_version_uuid:version.version_uuid','plan_fingerprint:inspected.plan_fingerprint',
    'anchor_run_uuid:pickedReport.anchor.run_uuid','report_uuid:pickedReport.uuid','crypto.randomUUID()',
    'Прочитать квитанцию запроса','весь frozen run','failed/nonconverged','не доказывает отсутствие эффекта'])assert.ok(source.includes(literal),literal);
  assert.doesNotMatch(source,/localStorage|sessionStorage|dangerouslySetInnerHTML|setInterval|submit_plan|retry\(/);
});


for (const late of ['receipt','error'] as const) test(`Q1 lookup cannot overwrite Q2 readback or error after late Q1 ${late}`, async () => {
  const generation = selectionGeneration(), trace: string[] = [];
  let displayedRequest = C, readback = 'Q1 old receipt', error = '';
  let resolve!: (value: unknown) => void, reject!: (value: Error) => void;
  const pending = new Promise<unknown>((ok,fail) => {resolve=ok;reject=fail;});
  const q1Generation = generation.next();
  const q1 = lookupCardRequest(C,() => generation.current(q1Generation),uuid => {
    assert.equal(uuid,C);trace.push(`GET:${uuid}`);return pending;
  },value => {readback=JSON.stringify(value);trace.push('Q1 receipt applied');},err => {
    error=String(err);trace.push('Q1 error applied');
  });
  // Exact production invalidation at the beginning of the next explicit write.
  generation.next();displayedRequest=V;readback='Q2 stored receipt';error='Q2 current error';
  if (late === 'receipt') resolve({status:'stored',kind:'version',receipt:version});
  else reject(new Error('Q1 lookup failed'));
  await q1;
  assert.deepEqual([displayedRequest,readback,error],[V,'Q2 stored receipt','Q2 current error']);
  assert.deepEqual(trace,[`GET:${C}`]);
  const q2Generation=generation.next();
  await lookupCardRequest(V,() => generation.current(q2Generation),async uuid => {
    assert.equal(uuid,V);return {status:'not_found'};
  },value => {readback=value.status;},err => {throw err;});
  assert.equal(readback,'not_found');assert.equal(displayedRequest,V);
});
