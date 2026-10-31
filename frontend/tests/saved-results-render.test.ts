import assert from 'node:assert/strict';
import {test} from 'node:test';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {readFile} from 'node:fs/promises';
import ts from 'typescript';
import type {SavedSlice} from '../src/saved-results-data.ts';
import {catalogFixture} from './saved-results.test.ts';

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
 assert.doesNotMatch(html,/Original native binding UUID|Selected native manifest SHA/);
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

test('R1 scatter renders signed physical J without connecting lines, missing interpolation or rescaling',()=>{
 const {brief}=catalogFixture();const original=structuredClone(brief);
 const html=renderToStaticMarkup(createElement(ui.SavedBrief,{page:brief}));finiteSvgGeometry(html);
 for(const text of ['A/m²','V per period','-0','not_measured','Сопоставимость физических постановок не установлена'])assert.ok(html.includes(text),text);
 assert.match(html,/<circle/);assert.doesNotMatch(html,/<polyline|scientific accepted|J0/);assert.deepEqual(brief,original);
 for(const bits of ['7ff0000000000000','fff0000000000000','7ff8000000000042']){const b=structuredClone(brief);b.points[0].view!.scalar_observables.current_density_A_per_m2!.bits=bits;const missing=renderToStaticMarkup(createElement(ui.SavedBrief,{page:b}));assert.doesNotMatch(missing,/<circle|<polyline/);finiteSvgGeometry(missing);}
});
test('R1 nonzero signed physical J ignores saved J0 display context',()=>{
 const {brief}=catalogFixture();brief.points[0].view!.scalar_observables.current_density_A_per_m2!.bits='c008000000000000';brief.points[0].view!.conventions={scales:{J0_A_per_m2:{kind:'ieee',type:'f64',bits:'4024000000000000'}}};
 const html=renderToStaticMarkup(createElement(ui.SavedBrief,{page:brief}));assert.match(html,/<title>0.05: -3<\/title>/);assert.doesNotMatch(html,/-30|<polyline/);finiteSvgGeometry(html);
});

// Execute the actual component handlers with deterministic hook slots/API promises.
// This is source/SSR wiring evidence, not a DOM/browser lifecycle qualification.
const hookURL='data:text/javascript;base64,'+Buffer.from("export const useState=(...a)=>globalThis.__r1Hooks.useState(...a);export const useRef=(...a)=>globalThis.__r1Hooks.useRef(...a);export const useEffect=(...a)=>globalThis.__r1Hooks.useEffect(...a);").toString('base64');
const apiURL='data:text/javascript;base64,'+Buffer.from("export const api=(...a)=>globalThis.__r1Api(...a);export const errorMessage=e=>String(e.message??e);").toString('base64');
let wired=transpiled.outputText.replaceAll('"./saved-diagnostics.tsx"',JSON.stringify(diagnosticUrl)).replaceAll("'./saved-diagnostics.tsx'",JSON.stringify(diagnosticUrl));
for(const [specifier,resolved] of [['react',hookURL],['react/jsx-runtime',import.meta.resolve('react/jsx-runtime')],['./api',apiURL],['./saved-results-data',new URL('../src/saved-results-data.ts',import.meta.url).href]])wired=wired.replaceAll(`"${specifier}"`,JSON.stringify(resolved)).replaceAll(`'${specifier}'`,JSON.stringify(resolved));
const wiredUI=await import('data:text/javascript;base64,'+Buffer.from(wired).toString('base64'));
function catalogHarness(component='SavedCatalogPanel'){
 const globals=globalThis as unknown as {__r1Hooks:Record<string,Function>;__r1Api:Function};
 const slots:unknown[]=[],effects:(()=>void)[]=[];let cursor=0,tree:unknown;
 const requests:{path:string;resolve:(v:unknown)=>void;reject:(e:unknown)=>void;body?:unknown}[]=[];
 globals.__r1Hooks={useState:(initial:unknown)=>{const i=cursor++;if(!(i in slots))slots[i]=typeof initial==='function'?initial():initial;return [slots[i],(v:unknown)=>slots[i]=typeof v==='function'?v(slots[i]):v];},useRef:(value:unknown)=>{const i=cursor++;return slots[i]??(slots[i]={current:value});},useEffect:(f:()=>Function,deps:unknown[])=>{const i=cursor++,old=slots[i] as {deps:unknown[];cleanup?:Function}|undefined;if(!old||deps.some((v,j)=>!Object.is(v,old.deps[j])))effects.push(()=>{old?.cleanup?.();slots[i]={deps:[...deps],cleanup:f()};});}};
 globals.__r1Api=(_token:string,path:string,body?:unknown)=>new Promise((resolve,reject)=>requests.push({path,body,resolve,reject}));
 function render(props:unknown){cursor=0;tree=wiredUI[component](props);while(effects.length)effects.shift()!();cursor=0;tree=wiredUI[component](props);return renderToStaticMarkup(tree as never);}
 function click(text:string){let found=false;function walk(v:unknown){if(Array.isArray(v)){v.forEach(walk);return;}if(!v||typeof v!=='object')return;const e=v as {type:unknown;props:{children?:unknown;onClick?:()=>void}};if(e.type==='button'&&(Array.isArray(e.props.children)?e.props.children.join(''):e.props.children)===text){found=true;e.props.onClick!();}else walk(e.props?.children);}walk(tree);assert.ok(found,text);}
 function find(predicate:(e:any)=>boolean):any{let found:any;function walk(v:any){if(Array.isArray(v))v.forEach(walk);else if(v&&typeof v==='object'){if(predicate(v))found=v;else walk(v.props?.children);}}walk(tree);assert.ok(found,'element exists');return found.props;}
 return {render,click,requests,find};
}
const settle=()=>new Promise<void>(resolve=>setImmediate(resolve));
test('R1 actual manual catalog/brief handlers do not read on mount/poll and render exact declared text',async()=>{
 const {page,brief,f}=catalogFixture(),h=catalogHarness(),props={token:'token',job:f.selection.calcjob_uuid,run:{uuid:f.selection.run_uuid,children:[{uuid:f.selection.calcjob_uuid,execution_id:'exec',attempt:2}]}};
 h.render(props);h.render({...props,run:{...props.run,mtime:'poll'}});assert.equal(h.requests.length,0);
 h.click('Прочитать каталог');assert.equal(h.requests.length,1);assert.match(h.requests[0].path,/sources\?/);assert.doesNotMatch(h.requests[0].path,/point_id|native_binding_uuid/);
 h.requests.shift()!.resolve(page);await settle();const metadata=h.render(props);assert.ok(metadata.includes('1'+'0'.repeat(2000)));assert.match(metadata,/not_measured|failed/);assert.doesNotMatch(metadata,/<circle/);
 h.click('Прочитать J(V) этой страницы');assert.match(h.requests[0].path,/brief\?.*page_ref=/);h.requests.shift()!.resolve(brief);await settle();const html=h.render(props);assert.match(html,/<circle/);assert.match(html,/-0/);assert.doesNotMatch(html,/<polyline/);
 h.render({...props,run:{...props.run,mtime:'another poll'}});assert.equal(h.requests.length,0);
});
test('R1 actual component drops deferred success and error for all selection changes and refresh',async()=>{
 for(const failure of [false,true])for(const cause of ['token','run','execution','attempt','CalcJob','removal','refresh']){
  const {page,f}=catalogFixture(),h=catalogHarness(),job=f.selection.calcjob_uuid,props={token:'token',job,run:{uuid:f.selection.run_uuid,children:[{uuid:job,execution_id:'exec',attempt:2}]}};
  h.render(props);h.click('Прочитать каталог');const old=h.requests.shift()!;let next=structuredClone(props);
  if(cause==='token')next.token='new';if(cause==='run')next.run.uuid=f.selection.calcjob_uuid;
  if(cause==='execution')next.run.children[0].execution_id='foreign';if(cause==='attempt')next.run.children[0].attempt=3;
  if(cause==='CalcJob'){next.job='55555555-5555-4555-8555-555555555555';next.run.children[0].uuid=next.job;}
  if(cause==='removal')next.run.children=[];
  h.render(next);
  if(cause==='refresh'){h.click('Прочитать каталог');h.requests.shift()!.resolve(page);await settle();h.render(next);}
  if(failure)old.reject(new Error('obsolete error'));else old.resolve(page);
  await settle();const html=h.render(next);assert.doesNotMatch(html,/obsolete error/);
  if(cause==='refresh')assert.ok(html.includes('1'+'0'.repeat(2000)));else assert.ok(!html.includes('1'+'0'.repeat(2000)),cause);
 }
});
test('R1 actual next/previous page handlers reject late brief success and error and preserve gaps',async()=>{
 for(const backward of [false,true])for(const failure of [false,true]){
  const {page,brief,f}=catalogFixture();page.pagination.limit=1;page.pagination.total='2';
  const sorted=(v:unknown):unknown=>Array.isArray(v)?v.map(sorted):v&&typeof v==='object'?Object.fromEntries(Object.keys(v).sort().map(k=>[k,sorted((v as Record<string,unknown>)[k])])):v;
  const oldToken=JSON.parse(Buffer.from(page.pagination.page_ref,'base64url').toString());
  const token=(offset:string)=>Buffer.from(JSON.stringify(sorted({...oldToken,limit:1,offset}))).toString('base64url');
  page.pagination.page_ref=token('0');page.pagination.next_cursor=token('1');
  const second=structuredClone(page);second.pagination.offset='1';second.pagination.page_ref=token('1');second.pagination.previous_cursor=token('0');second.pagination.next_cursor=null;second.points[0].point_id='point2';second.points[0].source_state!.identity.point_id='point2';
  const h=catalogHarness(),props={token:'token',job:f.selection.calcjob_uuid,run:{uuid:f.selection.run_uuid,children:[{uuid:f.selection.calcjob_uuid,execution_id:'exec',attempt:2}]}};
  h.render(props);h.click('Прочитать каталог');h.requests.shift()!.resolve(page);await settle();h.render(props);
  if(backward){h.click('Следующая страница');h.requests.shift()!.resolve(second);await settle();h.render(props);}
  h.click('Прочитать J(V) этой страницы');const late=h.requests.shift()!;
  h.click(backward?'Предыдущая страница':'Следующая страница');const next=h.requests.shift()!;assert.match(next.path,/cursor=/);next.resolve(backward?page:second);await settle();h.render(props);
  if(failure)late.reject(new Error('obsolete page error'));else late.resolve({...brief,pagination:backward?second.pagination:page.pagination});
  await settle();const html=h.render(props);assert.doesNotMatch(html,/obsolete page error|<circle|<polyline/);if(!backward)assert.match(html,/point2/);
 }
});

test('P10 explicit pair UI includes independent right run picker and unknown author assessment',()=>{
 const {page}=catalogFixture();const html=renderToStaticMarkup(createElement(ui.SavedHistoryPanel,{token:'never-render-token',run:{uuid:page.provider_selection.run_uuid},page}));
 for(const literal of ['Run UUID','Получить исполнения правого run','Закрепить слева','first_record','last_record','автора','unknown','Сравнить две закреплённые истории','batch overshoot'])assert.ok(html.includes(literal),literal);
 assert.doesNotMatch(html,/never-render-token|Bearer|dark/);
});
test('P10 raw signed bits counts null and locators render without computing differences',async()=>{
 const {historyFixture}=await import('./saved-results.test.ts');const {result}=historyFixture();const html=renderToStaticMarkup(createElement(ui.HistoryResultView,{result}));
 for(const literal of ['8000000000000000','c010000000000000','observations 4','updates 1','A/m^2','source_epoch','append_ordinal'])assert.ok(html.includes(literal),literal);
 const absent={...result,minimum:null,maximum:null,units:null};const nulls=renderToStaticMarkup(createElement(ui.HistoryResultView,{result:absent}));assert.match(nulls,/units: null/);assert.match(nulls,/конечное значение отсутствует/);
});

async function readyHistoryHarness(){
 const {historyFixture}=await import('./saved-results.test.ts');const fixture=historyFixture();
 const h=catalogHarness('SavedHistoryPanel'),props={token:'token',run:{uuid:fixture.page.provider_selection.run_uuid},page:fixture.page};
 h.render(props);assert.equal(h.requests.length,0);
 h.find(e=>e.type?.name==='HistoryRows').choose({page:fixture.page,row:fixture.page.points[0]});h.render(props);
 h.find(e=>e.type?.name==='ReductionFields'&&e.props.side==='left').edit(fixture.selected.reduction);h.render(props);
 return {h,props,fixture};
}
test('P10 actual explicit derive handler sends full immutable selection once and renders owning response',async()=>{
 const {h,props,fixture}=await readyHistoryHarness();
 h.click('Вычислить редукцию левой истории');h.click('Вычислить редукцию левой истории');assert.equal(h.requests.length,1);
 const request=h.requests.shift()!;assert.match(request.path,/derive-history$/);const {run_uuid,...expected}=fixture.selected;assert.deepEqual(request.body,expected);
 request.resolve(fixture.response);await settle();const html=h.render(props);assert.match(html,/8000000000000000/);assert.match(html,/not_assessed/);
});
test('P10 actual source/range/token/run/page/removal edits fence deferred success and failure',async()=>{
 for(const cause of ['left','right','field','range','assessment','token','run','page','removal'])for(const fail of [false,true]){
  const {h,props,fixture}=await readyHistoryHarness();h.click('Вычислить редукцию левой истории');const old=h.requests.shift()!;const next=structuredClone(props);
  if(cause==='left')h.find(e=>e.type?.name==='HistoryRows').choose({page:fixture.page,row:fixture.page.points[0]});
  if(cause==='right')h.find(e=>e.type?.name==='RightHistoryPicker').choose({page:fixture.page,row:fixture.page.points[0]});
  if(cause==='field'||cause==='range')h.find(e=>e.type?.name==='ReductionFields'&&e.props.side==='left').edit({...fixture.selected.reduction,...(cause==='field'?{field:'r_D'}:{last_record:'5'})});
  if(cause==='assessment')h.find(e=>e.type==='select'&&e.props.value==='unknown').onChange({target:{value:'different'}});
  if(cause==='token')next.token='new-token';if(cause==='run')next.run.uuid=fixture.page.provider_selection.calcjob_uuid;
  if(cause==='page')next.page.pagination.page_ref+='other';
  if(cause==='removal')h.click('Снять left');
  h.render(next);if(fail)old.reject(new Error('obsolete-history-error'));else old.resolve(fixture.response);await settle();
  const html=h.render(next);assert.doesNotMatch(html,/obsolete-history-error|observations 4/);
 }
});
test('P10 actual pair handler includes a separately pinned right run and explicit unknown assessment',async()=>{
 const {h,props,fixture}=await readyHistoryHarness();const rightPage=structuredClone(fixture.page);rightPage.provider_selection.run_uuid='77777777-7777-4777-8777-777777777777';
 h.find(e=>e.type?.name==='RightHistoryPicker').choose({page:rightPage,row:rightPage.points[0]});h.render(props);
 h.find(e=>e.type?.name==='ReductionFields'&&e.props.side==='right').edit(fixture.selected.reduction);h.render(props);
 h.find(e=>e.type==='textarea'&&e.props.value==='').onChange({target:{value:'Explicit author reason'}});h.render(props);
 h.click('Сравнить две закреплённые истории');assert.equal(h.requests.length,1);const request=h.requests.shift()!;assert.match(request.path,/compare-history$/);
 const pair=request.body as any;assert.equal(pair.left.run_uuid,props.run.uuid);assert.equal(pair.right.run_uuid,rightPage.provider_selection.run_uuid);assert.equal(pair.comparability.model,'unknown');
 request.reject(new Error('NEW controlled pair refusal'));await settle();assert.match(h.render(props),/NEW controlled pair refusal/);
});

test('P10 actual right-run picker reads only explicit run and one requested finite catalog page',async()=>{
 const {page,f}=catalogFixture();let pinned:any=null;const h=catalogHarness('RightHistoryPicker'),props={token:'token',choose:(v:any)=>pinned=v,invalidate:()=>{}};
 h.render(props);assert.equal(h.requests.length,0);
 h.find(e=>e.type==='input').onChange({target:{value:f.selection.run_uuid}});h.render(props);
 h.click('Получить исполнения правого run');assert.equal(h.requests.length,1);const runRequest=h.requests.shift()!;assert.equal(runRequest.path,`/runs/${f.selection.run_uuid}`);
 runRequest.resolve({uuid:f.selection.run_uuid,children:[{uuid:f.selection.calcjob_uuid,execution_id:'exec',attempt:2}]});await settle();h.render(props);assert.equal(h.requests.length,0);
 h.find(e=>e.type==='select').onChange({target:{value:f.selection.calcjob_uuid}});h.render(props);assert.equal(h.requests.length,0);
 h.click('Прочитать правый каталог');assert.equal(h.requests.length,1);const request=h.requests.shift()!;assert.match(request.path,/saved-results\/sources\?.*limit=8/);request.resolve(page);await settle();h.render(props);assert.equal(h.requests.length,0);
 h.find(e=>e.type?.name==='HistoryRows').choose({page,row:page.points[0]});assert.deepEqual(pinned.page.provider_selection,f.selection);assert.equal(h.requests.length,0);
});
