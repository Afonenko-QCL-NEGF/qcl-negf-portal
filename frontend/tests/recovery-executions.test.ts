import assert from 'node:assert/strict';
import {test} from 'node:test';
import * as api from '../src/api.ts';
import {readFile} from 'node:fs/promises';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import ts from 'typescript';
const uuid='563cc50c-0098-4d67-8f35-ad041b186721';
const summary={uuid,pk:132,label:'<recovery>',process_state:'waiting',exit_status:null,is_finished_ok:false,ctime:'2026-01-01',mtime:'2026-01-01',kind:'recovery_execution',caller:null};
const detail={...summary,plan:{uuid:'plan87',fingerprint:'stored-fingerprint'},execution_id:'point_1',attempt:null,recovery:{input_bundle_uuid:'bundle',input_prior_archive_uuid:null},children:[{uuid:'child134',pk:134,label:'calc',process_state:'waiting',exit_status:null,is_finished_ok:false,ctime:'2026-01-01',mtime:'2026-01-01',attempt:1,job_id:'7',scheduler_state:'RUNNING',retrieved_uuid:null,result_uuid:null,call_label:'iteration_01'}]};
test('recovery client reads separate authenticated page and detail',async()=>{
 assert.equal(typeof api.listRecoveryExecutions,'function');
 const original=globalThis.fetch;const seen:unknown[]=[];
 globalThis.fetch=async(input,init)=>{seen.push([input,init]);return new Response(JSON.stringify(String(input).includes('?')?{recovery_executions:[summary]}:detail));};
 try {assert.deepEqual(await api.listRecoveryExecutions('secret',25),[summary]);assert.deepEqual(await api.getRecoveryExecution('secret',uuid),detail);
 assert.equal(seen[0][0],'/api/v1/recovery-executions?limit=25&offset=25');assert.equal(seen[0][1].headers.Authorization,'Bearer secret');assert.equal(seen[0][1].cache,'no-store');
 }finally{globalThis.fetch=original;}
});
test('recovery request guard rejects superseded selection, page, logout and old errors',async()=>{
 assert.equal(typeof api.RecoveryRequests,'function');
 const original=globalThis.fetch;const pending:((value:Response)=>void)[]=[];const received:unknown[]=[];const errors:unknown[]=[];
 globalThis.fetch=()=>new Promise(resolve=>pending.push(resolve));
 try {const requests=new api.RecoveryRequests();const first=requests.detail('secret','old',value=>received.push(value),value=>errors.push(value));const second=requests.detail('secret',uuid,value=>received.push(value),value=>errors.push(value));
 pending[1](new Response(JSON.stringify(detail)));await second;pending[0](new Response('{}',{status:404}));await first;assert.deepEqual(received,[detail]);assert.deepEqual(errors,[]);
 const page=requests.list('secret',0,value=>received.push(value),value=>errors.push(value));requests.invalidate();pending[2](new Response(JSON.stringify({recovery_executions:[summary]})));await page;assert.equal(received.length,1);
 const old=requests.detail('secret',uuid,value=>received.push(value),value=>errors.push(value));requests.invalidate();pending[3](new Response(JSON.stringify(detail)));await old;assert.equal(received.length,1);
 const unavailable=requests.list('secret',0,()=>assert.fail('old API cannot succeed'),value=>errors.push(value));pending[4](new Response('{}',{status:404}));await unavailable;assert.equal((errors[0] as api.ApiError).status,404);
 }finally{globalThis.fetch=original;}
});
test('separate recovery rendering preserves provenance and unavailable state without actions',async()=>{
 assert.equal(typeof api.RecoveryRequests,'function');
 const filename=new URL('../src/recovery-executions.tsx',import.meta.url);
 let code=ts.transpileModule(await readFile(filename,'utf8'),{fileName:filename.pathname,compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText;
 for(const specifier of ['react','react/jsx-runtime','./api.ts']){const target=JSON.stringify(specifier.startsWith('.')?new URL(specifier,filename).href:import.meta.resolve(specifier));code=code.replaceAll(JSON.stringify(specifier),target).replaceAll(`'${specifier}'`,target);}
 const ui=await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`);
 const html=renderToStaticMarkup(createElement(ui.RecoveryDetail,{value:detail}));
 for(const text of [uuid,'child134','7','RUNNING','plan87','stored-fingerprint','bundle','Scientific acceptance: not measured here','stored AiiDA','standalone recovery','unknown','&lt;recovery&gt;'])assert.ok(html.includes(text),text);
 assert.doesNotMatch(html,/kill|Download|Export|scientific.*PASS|href=/i);
 const unavailable=renderToStaticMarkup(createElement(ui.RecoveryListStatus,{loading:false,error:new api.ApiError('missing',new Headers(),404),empty:true}));assert.match(unavailable,/unavailable/);
 assert.match(renderToStaticMarkup(createElement(ui.RecoveryListStatus,{loading:true,error:null,empty:true})),/Loading/);
 assert.match(renderToStaticMarkup(createElement(ui.RecoveryListStatus,{loading:false,error:null,empty:true})),/No recovery/);
});
