import assert from 'node:assert/strict';
import {test} from 'node:test';
import {readFile} from 'node:fs/promises';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import ts from 'typescript';
import {readQuantum,quantumRequest,quantumChoices} from '../src/quantum-preview-data.ts';
const filename=new URL('../src/quantum-preview.tsx',import.meta.url);
const result=ts.transpileModule(await readFile(filename,'utf8'),{fileName:filename.pathname,reportDiagnostics:true,compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}});
assert.deepEqual((result.diagnostics??[]).filter(d=>d.category===ts.DiagnosticCategory.Error).map(d=>ts.flattenDiagnosticMessageText(d.messageText,'\n')),[]);
let code=result.outputText;for(const specifier of ['react','react/jsx-runtime','./api.ts','./configurator-data.ts','./quantum-preview-data.ts']){const target=specifier.startsWith('.')?new URL(specifier,filename).href:import.meta.resolve(specifier);code=code.replaceAll(`"${specifier}"`,JSON.stringify(target)).replaceAll(`'${specifier}'`,JSON.stringify(target));}
const ui=await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`);
const plan='{"fingerprint":"'+'a'.repeat(64)+'","scientific_fingerprint":"'+'b'.repeat(64)+'","executions":[{"id":"e","variant_id":"v"}],"points":[{"id":"p","execution_id":"e","effective_inputs":{"temperature_K":70,"voltage_per_period_V":0,"configuration_hash":"'+'c'.repeat(64)+'","hash_encoding":"qcl-negf-canonical-bytes-v1","axes_applied":true}}]}';
const source={runner_source_id:'e'.repeat(40),core_source_id:'f'.repeat(40)},request=quantumRequest(plan,'a'.repeat(64),quantumChoices(plan)[0],source);
const value={schema:'qcl-negf.quantum-preview.v1',status:'available',preview_id:'b'.repeat(64),payload_sha256:'c'.repeat(64),payload_digest_encoding:'qcl-negf-canonical-bytes-v1',policy_revision:1,budget_policy_id:'quantum-preview-reference-v1',identity:{plan_sha256:request.plan_sha256,request_sha256:'a'.repeat(64),fingerprint:'a'.repeat(64),scientific_fingerprint:'b'.repeat(64),point_passport:{temperature_K:70,voltage_per_period_V:0,configuration_hash:'c'.repeat(64),hash_encoding:'qcl-negf-canonical-bytes-v1',axes_applied:true},execution_id:'e',point_id:'p',variant_id:'v',...source,runtime:{...source,context_sha256:'d'.repeat(64)}},payload:{shape:[3,2],dimensions:{Nz:3,Nb:2},z_nm:[0,1,3],energy_eV:[.1,.2],band_edge_eV:[.3,.4,.5],psi_real:[[1e308,0],[0,1],[-1e308,0]],psi_imag:[[0,1],[1,0],[0,-1]],density_per_nm:[[1,0],[0,1],[1,0]],units:{energy:'eV',z:'nm',psi:'nm^-1/2',density:'nm^-1'},semantics:{scope:'bare_onsite_k0',hartree:'zero',kappa:0,interperiod_hopping:'excluded',window_boundary:'Dirichlet'},diagnostics:{}},assessment:{SCBA:'not_performed',Poisson:'not_performed',scientific:'not_assessed'},measurements:{elapsed_seconds:null,peak_bytes:null}};
test('actual strict decoder plus light rendering preserve all states, raw matrices and semantics',()=>{
 const parsed=readQuantum(JSON.stringify(value),request),html=renderToStaticMarkup(createElement(ui.QuantumResult,{value:parsed}));
 for(const text of ['0.1','0.2','Re ψ','Im ψ','Saved density','nm^-1/2','no T±','Dirichlet','not_assessed','state 2','1e+308'])assert.ok(html.includes(text),text);
 assert.doesNotMatch(html,/NaN|Infinity|scientific\s*:?\s*pass\b|<title><\/title>/);
 assert.equal((html.match(/<polyline/g)??[]).length,9);
 assert.throws(()=>readQuantum(JSON.stringify({...value,identity:{...value.identity,point_id:'foreign'}}),request));
 assert.throws(()=>readQuantum(JSON.stringify({...value,payload:{...value.payload,shape:[3,3]}}),request));
});
