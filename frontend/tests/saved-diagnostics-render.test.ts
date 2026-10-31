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
test('native descriptor preserves failed/unknown separate axes and exact counts without live claim',()=>{
 const data={closed:false,status:{process:{state:'failed'},physics:{state:'not_measured'},experimental:{state:'not_measured'}},history:{records:'9007199254740993'},resources:{performance_summary:{status:'not_available',reason:'not_recorded_in_result_v2'}},io_counters:{}};
 const html=renderToStaticMarkup(createElement(ui.DiagnosticSummary,{data}));for(const text of ['failed','not_measured','9007199254740993','not_recorded_in_result_v2','не live'])assert.ok(html.includes(text),text);assert.doesNotMatch(html,/9007199254740992|scientific.*pass/i);
});

test('diagnostics entry uses Russian controls and exact source context without pins entry',()=>{
 const html=renderToStaticMarkup(createElement(ui.SavedDiagnostics,{token:'secret',context:{selection:{run_uuid:'run',execution_id:'exec',attempt:2,calcjob_uuid:'job'},point_id:'point',native_binding_uuid:'binding',state_manifest_sha256:'a'.repeat(64)}}));
 for(const text of ['Точка point','транспортная попытка 2','Прочитать описание диагностики выбранного состояния','без polling, расчёта или derive'])assert.ok(html.includes(text),text);
 assert.doesNotMatch(html,/secret|Bearer|<input|Read first|Select explicitly/);
});
