import assert from 'node:assert/strict';
import {test} from 'node:test';
import {readFile} from 'node:fs/promises';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import ts from 'typescript';
import {ReportGate} from '../src/agent-report-data.ts';
const filename=new URL('../src/agent-report-upload.tsx',import.meta.url);
const source=await readFile(filename,'utf8');
let output=ts.transpileModule(source,{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText;
for(const specifier of ['react','react/jsx-runtime','./api.ts','./agent-report-data.ts']) {
 const resolved=specifier.startsWith('.')?new URL(specifier,filename).href:import.meta.resolve(specifier);
 output=output.replaceAll(`"${specifier}"`,JSON.stringify(resolved)).replaceAll(`'${specifier}'`,JSON.stringify(resolved));
}
const ui=await import(`data:text/javascript;base64,${Buffer.from(output).toString('base64')}`);
test('upload starts with explicit selection and separates author report from science',()=>{
 const html=renderToStaticMarkup(createElement(ui.AgentReportUpload,{token:'secret',uuid:'run',gate:new ReportGate(),refresh:()=>{}}));
 assert.match(html,/Report JSON file/);assert.match(html,/separate from scientific acceptance/);
 assert.doesNotMatch(html,/Save report once/);assert.doesNotMatch(html,/secret/);
});
test('unknown gate survives a fresh panel render and is visible',()=>{
 const gate=new ReportGate();gate.finish(new Error('network'));
 const html=renderToStaticMarkup(createElement(ui.AgentReportUpload,{token:'x',uuid:'new-run',gate,refresh:()=>{}}));
 assert.match(html,/write outcome is unknown/);assert.match(html,/Do not retry/);
});
