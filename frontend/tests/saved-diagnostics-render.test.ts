import assert from 'node:assert/strict';
import {test} from 'node:test';
import {readFile} from 'node:fs/promises';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import ts from 'typescript';
const filename=new URL('../src/saved-diagnostics.tsx',import.meta.url);
const result=ts.transpileModule(await readFile(filename,'utf8'),{fileName:filename.pathname,reportDiagnostics:true,compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}});
assert.deepEqual((result.diagnostics??[]).filter(d=>d.category===ts.DiagnosticCategory.Error).map(d=>ts.flattenDiagnosticMessageText(d.messageText,'\n')),[]);
let code=result.outputText;for(const specifier of ['react','react/jsx-runtime','./api.ts','./saved-results-data.ts','./saved-diagnostics-data.ts']){const target=specifier.startsWith('.')?new URL(specifier,filename).href:import.meta.resolve(specifier);code=code.replaceAll(`"${specifier}"`,JSON.stringify(target)).replaceAll(`'${specifier}'`,JSON.stringify(target));}
const ui=await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`);
test('saved descriptor states failed/unknown/partial cost without live or accounting claim',()=>{
 const data={closed:null,generation:7,recorded_statuses:{terminal_status:'failed',converged:null,scientific_accepted:null},history:{status:'unavailable',reason:'history_owner_missing'},resources:{status:'available',checkpoint:{checkpoint_payload_seconds:2,checkpoint_payload_bytes:{kind:'integer',decimal:'9007199254740993'},includes_commit_publication:false,scope:'point_commit_checkpoint_payload'},performance_summary:{status:'unavailable',reason:'source_binding_missing'}},coverage:{scientific_acceptance:'not_assessed'},integrity:{},io_counters:{},limits:{}};
 const html=renderToStaticMarkup(createElement(ui.DiagnosticSummary,{data}));for(const text of ['failed','unknown','history_owner_missing','9007199254740993','source_binding_missing','не job wall/CPU','не live'])assert.ok(html.includes(text),text);
 assert.doesNotMatch(html,/9007199254740992|scientific.*pass/i);
});
