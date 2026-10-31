import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { ApiError } from '../src/api.ts';
import {prepareReport, ReportGate, postReport, getRawReport, validateReceipt, friendlyReport} from '../src/agent-report-data.ts';
const run='11111111-1111-4111-8111-111111111111', id='22222222-2222-4222-8222-222222222222';
const anchor={run_uuid:run,root_definition_id:'study',root_kind:'study',plan_fingerprint:'a'.repeat(64)};
const raw=` { "anchor":${JSON.stringify(anchor)}, "used_runs":[{"attempt":9007199254740993}], "text":"λ" }\n`;
function file(bytes:Uint8Array) {return {size:bytes.length,arrayBuffer:async()=>bytes.buffer as ArrayBuffer};}
const bytes=new TextEncoder().encode(raw);
const make=()=>prepareReport(file(bytes),run);
const selection=async()=>({token:'token',run,preview:await make()});
const receipt=(p:Awaited<ReturnType<typeof make>>)=>({uuid:id,filename:'agent-report.json',bytes:p.bytes,sha256:p.sha256,anchor});
test('raw text, giant integer, Unicode, size and SHA survive transport exactly',async()=>{
 const p=await make(); assert.equal(p.raw,raw);assert.match(p.raw,/9007199254740993/);
 assert.equal(p.sha256,createHash('sha256').update(bytes).digest('hex'));
 const s={token:'token',run,preview:p}; let body=''; const old=globalThis.fetch;
 globalThis.fetch=async (_url,init)=>{body=String(init?.body);return new Response(JSON.stringify(receipt(p)),{status:201});};
 try {assert.equal(await postReport(s),id);assert.equal(JSON.parse(body).report,raw);} finally {globalThis.fetch=old;}
});
test('byte cap precedes reading; invalid UTF-8 and BOM are refused',async()=>{
 let read=false;await assert.rejects(prepareReport({size:262145,arrayBuffer:async()=>{read=true;return new ArrayBuffer(0);}},run));assert.equal(read,false);
 await assert.rejects(prepareReport(file(new Uint8Array([0xff])),run));
 await assert.rejects(prepareReport(file(new Uint8Array([0xef,0xbb,0xbf,123,125])),run));
 const base=new TextEncoder().encode(`{"anchor":${JSON.stringify(anchor)}}`);
 const full=new Uint8Array(262144).fill(32);full.set(base);assert.equal((await prepareReport(file(full),run)).bytes,262144);
});
test('same-turn latch, sticky unknown, and known refusal behavior across panel owners',async()=>{
 const s=await selection(), gate=new ReportGate();assert.ok(gate.begin(s));assert.equal(gate.begin(s),false);
 gate.finish(new ApiError('failed',new Headers(),503));assert.equal(gate.uncertain,true);
 assert.equal(gate.begin({...s,run:'different'}),false);
 const done=new ReportGate();assert.ok(done.begin(s));done.finish();assert.equal(done.begin(s),false);
 const known=new ReportGate();assert.ok(known.begin(s));known.finish(new ApiError('invalid',new Headers(),422));assert.ok(known.begin(s));
});
test('invalid receipt cannot establish a known success',async()=>{
 const s=await selection();assert.equal(validateReceipt(receipt(s.preview),s),id);
 for (const bad of [{...receipt(s.preview),bytes:0},{...receipt(s.preview),sha256:'x'},
 {...receipt(s.preview),uuid:'bad'},{...receipt(s.preview),anchor:{...anchor,run_uuid:id}}]) assert.throws(()=>validateReceipt(bad,s));
 const gate=new ReportGate();assert.ok(gate.begin(s));try {validateReceipt({},s);} catch(e) {gate.finish(e);} assert.equal(gate.uncertain,true);
});
test('known save followed by failed readback never causes another POST',async()=>{
 const s=await selection(), old=globalThis.fetch;let posts=0;
 globalThis.fetch=async (_url,init)=>{if(init?.method==='POST'){posts++;return new Response(JSON.stringify(receipt(s.preview)),{status:201});}return new Response('',{status:503});};
 try {const result=await postReport(s);await assert.rejects(getRawReport(s.token,run,result));assert.equal(posts,1);} finally {globalThis.fetch=old;}
});

test('late network failure blocks a newly subscribed run while request is in flight',async()=>{
 const s=await selection(),gate=new ReportGate();let reject!:(e:Error)=>void;
 const pending=new Promise<never>((_,r)=>{reject=r;});assert.ok(gate.begin(s));
 const flight=pending.catch(e=>gate.finish(e));let notices=0;
 const unsubscribe=gate.subscribe(()=>notices++);
 assert.equal(gate.begin({...s,run:'another'}),false);reject(new Error('network'));await flight;
 assert.equal(gate.uncertain,true);assert.equal(notices,1);assert.equal(gate.begin({...s,run:'another'}),false);unsubscribe();
});

test('friendly view refuses unsafe attempts while raw text retains their digits',()=>{
 const base={anchor,question_snapshot:'q',conclusion:'c',reasoning:'r',limitations:'l',used_runs:[{run_uuid:run,plan_fingerprint:'a',execution_id:'e',definition_id:'d',variant_id:'v',calcjob_uuid:id,attempt:1}]};
 assert.ok(friendlyReport(JSON.stringify(base)));
 const unsafe=JSON.stringify(base).replace('"attempt":1','"attempt":9007199254740993');
 assert.equal(friendlyReport(unsafe),null);assert.match(unsafe,/9007199254740993/);
});
