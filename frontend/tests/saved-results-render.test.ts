import assert from 'node:assert/strict';
import {test} from 'node:test';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {readFile} from 'node:fs/promises';
import ts from 'typescript';
import type {SavedSlice} from '../src/saved-results-data.ts';

// Reuse the existing locked compiler/display-only render fixture convention.
const filename=new URL('../src/saved-results.tsx',import.meta.url);
const source=await readFile(filename,'utf8');
const transpiled=ts.transpileModule(source,{fileName:filename.pathname,reportDiagnostics:true,compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}});
assert.deepEqual((transpiled.diagnostics??[]).filter(d=>d.category===ts.DiagnosticCategory.Error).map(d=>ts.flattenDiagnosticMessageText(d.messageText,'\n')),[],'SavedPlot TSX must parse before SSR');
let output=transpiled.outputText;
const diagnosticFile=new URL('../src/saved-diagnostics.tsx',import.meta.url);
const diagnosticLowered=ts.transpileModule(await readFile(diagnosticFile,'utf8'),{fileName:diagnosticFile.pathname,reportDiagnostics:true,compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}});
assert.deepEqual((diagnosticLowered.diagnostics??[]).filter(d=>d.category===ts.DiagnosticCategory.Error).map(d=>ts.flattenDiagnosticMessageText(d.messageText,'\n')),[]);
let diagnosticCode=diagnosticLowered.outputText;
for(const specifier of ['react','react/jsx-runtime','./api.ts','./saved-results-data.ts','./saved-diagnostics-data.ts']){const resolved=specifier.startsWith('.')?new URL(specifier,diagnosticFile).href:import.meta.resolve(specifier);diagnosticCode=diagnosticCode.replaceAll(`"${specifier}"`,JSON.stringify(resolved)).replaceAll(`'${specifier}'`,JSON.stringify(resolved));}
const diagnosticUrl=`data:text/javascript;base64,${Buffer.from(diagnosticCode).toString('base64')}`;
output=output.replaceAll('"./saved-diagnostics.tsx"',JSON.stringify(diagnosticUrl)).replaceAll("'./saved-diagnostics.tsx'",JSON.stringify(diagnosticUrl));

for(const [specifier,target] of [['react','react'],['react/jsx-runtime','react/jsx-runtime'],['./api','./api.ts'],['./saved-results-data','./saved-results-data.ts']]){
 const resolved=specifier.startsWith('.')?new URL(target,filename).href:import.meta.resolve(target);
 output=output.replaceAll(`"${specifier}"`,JSON.stringify(resolved)).replaceAll(`'${specifier}'`,JSON.stringify(resolved));
}
const ui=await import(`data:text/javascript;base64,${Buffer.from(output).toString('base64')}`);
test('signed heatmap retains unique cells, coordinate geometry, zero and native mask',()=>{
 const slice={view_id:'spatial_energy_density',returned_shape:[2,2],logical_axes:['E','z'],axes:[{name:'E',values:[-.2,.6],units:'eV'},{name:'z',values:[0,9],units:'nm'}],values:[[-11,0],[null,21]],invalid_mask:[[null,null],['nan',null]],units:'m^-3/eV',conventions:{},recorded_statuses:{scientific_accepted:false}} as unknown as SavedSlice;
 const html=renderToStaticMarkup(createElement(ui.SavedPlot,{slice}));
 assert.match(html,/-11/);assert.match(html,/native nan/);assert.match(html,/0 m\^-3\/eV/);assert.match(html,/21/);
 assert.doesNotMatch(html,/<title><\/title>|\)\}/);
 assert.match(html,/E \(eV\)/);assert.match(html,/z \(nm\)/);assert.doesNotMatch(html,/scientific accepted/i);
});

test('explicit state and component rendering preserves basis, populations and source target reason',()=>{
 const base={returned_shape:[2],logical_axes:['state'],axes:[{name:'state',values:[3,7],units:'1'}],values:[-2,9],invalid_mask:[null,null],units:'m^-2',basis:'effective',conventions:{gauge:'saved gauge'},recorded_statuses:{scientific_accepted:false},fractions:{values:null,denominator:null,reason:'saved_target_doping_unavailable'}};
 const html=renderToStaticMarkup(createElement(ui.SavedPlot,{slice:{...base,view_id:'effective_populations'}}));
 for(const value of ['Saved state populations','state 3: -2','state 7: 9','saved_target_doping_unavailable','effective'])assert.ok(html.includes(value),value);
 assert.doesNotMatch(html,/sum of selected|normalized to 1|<script>/);
 const initial=renderToStaticMarkup(createElement(ui.SavedResultsPanel,{token:'secret',run:{uuid:'run',children:[{uuid:'job',execution_id:'ex',attempt:2}]}}));
 assert.match(initial,/Точка исходного плана/);assert.match(initial,/Обновить сведения выбранной точки/);assert.match(initial,/R1/);
 assert.doesNotMatch(initial,/first child|latest file/);
 assert.match(initial,/Выберите явно/);assert.doesNotMatch(initial,/secret|Bearer|<option selected="" value="ex/);
});

test('saved imaginary state curves and asymmetric native matrix keep independent mapping',()=>{
 const wave={view_id:'effective_wavefunctions_imag',returned_shape:[2,2],logical_axes:['z','state'],axes:[{name:'z',values:[0,9],units:'nm'},{name:'state',values:[3,7],units:'1'}],values:[[-11,-12],[-21,-22]],invalid_mask:[[null,null],[null,null]],units:'nm^-1/2',basis:'effective',conventions:{gauge:'original gauge'},recorded_statuses:{scientific_accepted:false}};
 const html=renderToStaticMarkup(createElement(ui.SavedPlot,{slice:wave}));
 assert.match(html,/<title>0: -11<\/title>/);assert.match(html,/<title>9: -22<\/title>/);
 for(const literal of ['effective_wavefunctions_imag','state 3','state 7','original gauge','-11','-22'])assert.ok(html.includes(literal),literal);
 const matrix={...wave,view_id:'effective_density_matrix_real',logical_axes:['state','state_prime'],axes:[{name:'state',values:[3,7],units:'1'},{name:'state_prime',values:[3,7],units:'1'}],values:[[2,11],[21,4]],units:'m^-2'};
 const grid=renderToStaticMarkup(createElement(ui.SavedPlot,{slice:matrix}));
 assert.match(grid,/state=3, state_prime=7: 11/);assert.match(grid,/state=7, state_prime=3: 21/);
});

function finiteSvgGeometry(html:string){
 for(const match of html.matchAll(/\b(?:x|y|cx|cy|width|height|points|d|viewBox)="([^"]*)"/g)){
  assert.doesNotMatch(match[1],/NaN|Infinity/);
  for(const token of match[1].match(/[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?/gi)??[])assert.ok(Number.isFinite(Number(token)),token);
 }
}
test('opposite signed extremes and huge singleton produce finite line/bar geometry with exact raw table',()=>{
 for(const values of [[-1e308,1e308],[1e308]]){
  const base={returned_shape:[values.length],logical_axes:['state'],axes:[{name:'state',values:values.map((_,i)=>i),units:'1'}],values,invalid_mask:values.map(()=>null),units:'m^-2',basis:'effective',conventions:{},recorded_statuses:{scientific_accepted:false}};
  for(const view_id of ['density','effective_populations']){
   const html=renderToStaticMarkup(createElement(ui.SavedPlot,{slice:{...base,view_id}}));
   finiteSvgGeometry(html);assert.ok(html.includes('1e+308'));if(values.length===2)assert.ok(html.includes('-1e+308'));
  }
 }
});
test('overflowing native centers visibly refuse graph and retain signed values/order/mask',()=>{
 const slice={view_id:'spatial_energy_density',returned_shape:[2,2],logical_axes:['E','z'],axes:[{name:'E',values:[-1e308,1e308],units:'eV'},{name:'z',values:[0,9],units:'nm'}],values:[[-11,0],[null,21]],invalid_mask:[[null,null],['nan',null]],units:'m^-3/eV',conventions:{},recorded_statuses:{}};
 const html=renderToStaticMarkup(createElement(ui.SavedPlot,{slice}));
 finiteSvgGeometry(html);assert.match(html,/Graph unavailable/);assert.match(html,/-1e\+308/);assert.match(html,/1e\+308/);assert.match(html,/native nan/);assert.match(html,/-11/);assert.match(html,/21/);
 const single={...slice,returned_shape:[1,2],axes:[{name:'E',values:[1e308],units:'eV'},{name:'z',values:[0,9],units:'nm'}],values:[[0,null]],invalid_mask:[[null,'positive_infinity']]};
 const singleton=renderToStaticMarkup(createElement(ui.SavedPlot,{slice:single}));
 finiteSvgGeometry(singleton);assert.match(singleton,/Graph unavailable/);assert.match(singleton,/1e\+308/);assert.match(singleton,/native positive_infinity/);assert.match(singleton,/0 m\^-3\/eV/);
});

test('negative native zero is explicit in unchanged value table',()=>{
 const slice={view_id:'density',returned_shape:[1],logical_axes:['z'],axes:[{name:'z',values:[0],units:'nm'}],values:[-0],invalid_mask:[null],units:'m^-3',conventions:{},recorded_statuses:{}};
 const html=renderToStaticMarkup(createElement(ui.SavedPlot,{slice}));assert.match(html,/-0 m\^-3/);
});

test('ordinary researcher refresh has no mandatory opaque pins',()=>{
 const html=renderToStaticMarkup(createElement(ui.SavedResultsPanel,{token:'secret',run:{uuid:'run',children:[],plan_points:[]}}));
 assert.match(html,/Обновить сведения выбранной точки/);
 assert.doesNotMatch(html,/Original native binding UUID|Selected native manifest SHA|<input/);
});
test('C01 line axis labels preserve native signed zero',()=>{
 const slice={view_id:'density',returned_shape:[2],logical_axes:['z'],axes:[{name:'z',values:[-0,1],units:'nm'}],values:[-0,2],invalid_mask:[null,null],units:'m^-3',conventions:{},recorded_statuses:{}};
 const html=renderToStaticMarkup(createElement(ui.SavedPlot,{slice}));
 assert.match(html,/<text x="52" y="275">-0<\/text>/);
 assert.match(html,/<text x="4" y="250">-0<\/text>/);
});

test('metadata summary keeps absent, checkpoint and retained scope readable without editable pins',()=>{
 const state={identity:{run_id:'native',execution_id:'exec',point_id:'point',attempt:'1',branch_id:'branch',model_sha256:'b'.repeat(64),plan_sha256:'c'.repeat(64)},state_id:'state',state_sequence:'1',manifest:{path:'state.json',bytes:'1',sha256:'a'.repeat(64),schema:'qcl-negf.result-manifest.v1',media_type:'application/json'}};
 const base={schema:'qcl-negf.portal-point-source.v1',status:'available',scope:'original_owner_metadata',source_state:state,retained:{native_binding:{node_uuid:'binding-original'},creator:{calcjob_uuid:'original-creator',binding_uuid:'prior-binding'}},source:{kind:'final',relation:'retained_source',reason:'failed_new_attempt'}};
 const retained=renderToStaticMarkup(createElement(ui.PointSourceSummary,{source:base}));
 for(const text of ['Сохранённый источник другой попытки','научная приёмка не установлена','Исходные идентификаторы','original-creator','prior-binding','failed_new_attempt'])assert.ok(retained.includes(text),text);
 assert.doesNotMatch(retained,/<input|<button|checked_ro_current_request/);
 const absent=renderToStaticMarkup(createElement(ui.PointSourceSummary,{source:{...base,source_state:null,source:{kind:'absent',relation:null,reason:'not_recorded'}}}));assert.match(absent,/Источник отсутствует: not_recorded/);
 const checkpoint=renderToStaticMarkup(createElement(ui.PointSourceSummary,{source:{...base,source:{kind:'checkpoint',relation:'current_point_attempt',reason:'interrupted'}}}));assert.match(checkpoint,/финальные карты не объявлены/);
});
