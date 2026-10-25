import assert from 'node:assert/strict';
import {test} from 'node:test';
import {quantumChoices,quantumRequest,QuantumSession,readQuantum} from '../src/quantum-preview-data.ts';
const raw='{ "executions":[{"id":"e","variant_id":"v"}],"points":[{"id":"p","execution_id":"e"}] }\n';
const source={runner_source_id:'e'.repeat(40),core_source_id:'f'.repeat(40)};
test('explicit selector preserves exact raw bytes and source IDs, never chooses first',()=>{
 const rows=quantumChoices(raw);assert.deepEqual(rows,[{execution_id:'e',variant_id:'v',point_id:'p'}]);
 assert.throws(()=>quantumRequest(raw,'a'.repeat(64),null,source));
 const request=quantumRequest(raw,'a'.repeat(64),rows[0],source);assert.equal(request.plan,raw);assert.equal(request.point_id,'p');
 assert.throws(()=>quantumChoices('{"executions":[],"points":[{}]}'));
});
test('stale context cancels in-flight request, generation never applies old result',()=>{
 const session=new QuantumSession();const first=session.begin();assert.ok(first);
 assert.equal(session.begin(),null);session.invalidate();assert.equal(first!.signal.aborted,true);
 assert.equal(session.current(first!),false);const next=session.begin();assert.ok(next);assert.equal(next!.signal.aborted,false);
 session.finish(first!);assert.equal(session.current(next!),true);session.finish(next!);
});
test('strict DTO rejects foreign raw identity, nonfinite shape and semantic substitution',()=>{
 const request=quantumRequest(raw,'a'.repeat(64),quantumChoices(raw)[0],source);
 assert.throws(()=>readQuantum('{}',request));
 assert.throws(()=>readQuantum('{"schema":"qcl-negf.quantum-preview.v1","status":"available","payload":{"shape":[1,1]}}',request));
});

test('render mapping preserves signed extrema/singletons without Infinity or NaN',async()=>{
 const {plotScale}=await import('../src/quantum-preview-data.ts');
 const map=plotScale([-1e308,0,1e308],900);assert.deepEqual([-1e308,0,1e308].map(map),[0,450,900]);
 assert.equal(plotScale([1e308],900)(1e308),450);
});

test('quantum-only fetch has 195s abort, one POST and preserves exact transport bytes',async()=>{
 const {mock}=await import('node:test'),{quantumPreviewRaw}=await import('../src/api.ts');
 let expire=()=>{},delay=0,calls=0;const controller=new AbortController();
 const timer=mock.method(globalThis,'setTimeout',(callback:()=>void,milliseconds:number)=>{expire=callback;delay=milliseconds;return 0;});
 const fetchMock=mock.method(globalThis,'fetch',async(_url:unknown,options:RequestInit)=>{
  calls++;assert.equal(options.body,' {"raw":1} ');assert.equal(options.cache,'no-store');
  return await new Promise<Response>((_resolve,reject)=>options.signal?.addEventListener('abort',()=>reject(new DOMException('aborted','AbortError')),{once:true}));
 });
 try{const pending=quantumPreviewRaw('token',' {"raw":1} ',controller.signal);assert.equal(delay,195000);expire();await assert.rejects(pending);assert.equal(calls,1);}
 finally{fetchMock.mock.restore();timer.mock.restore();}
 const exact=' { "n":1.0000000000000001 }\n';
 const success=mock.method(globalThis,'fetch',async()=>new Response(exact,{status:200,headers:{'Content-Type':'application/json'}}));
 try{assert.equal(await quantumPreviewRaw('token','{}',new AbortController().signal),exact);}finally{success.mock.restore();}
});
