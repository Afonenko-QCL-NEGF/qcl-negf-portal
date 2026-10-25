import assert from 'node:assert/strict';
import {test} from 'node:test';
import {readFile} from 'node:fs/promises';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import ts from 'typescript';

const filename=new URL('../src/run-structure.tsx',import.meta.url);
const source=await readFile(filename,'utf8');
let output=ts.transpileModule(source,{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText;
for(const specifier of ['react','react/jsx-runtime','./api.ts','./run-structure-data.ts']) {
  const resolved=specifier.startsWith('.')?new URL(specifier,filename).href:import.meta.resolve(specifier);
  output=output.replaceAll(`"${specifier}"`,JSON.stringify(resolved)).replaceAll(`'${specifier}'`,JSON.stringify(resolved));
}
const ui=await import(`data:text/javascript;base64,${Buffer.from(output).toString('base64')}`);
function run(uuid='run-A') {
  return {uuid,plan_fingerprint:'fingerprint-A',children:[
    {uuid:'child-1',execution_id:'e1',attempt:1,process_state:'excepted',exit_status:4,is_finished_ok:false},
    {uuid:'child-2',execution_id:'e1',attempt:2,process_state:'finished',exit_status:0,is_finished_ok:true}],
    plan_structure:{status:'available',root:{kind:'study',definition_id:'d1'},
      inclusions:[{id:'i1',parent_id:null,definition_id:'d1',label:'<script>bad</script>',path:['d1'],execution_ids:['e1','e2']}],
      executions:[{id:'e1',definition_id:'d1',variant_id:'v1',label:'First',operation:'transport',point_ids:['p1','p2'],voltage_mode:'continuation'},
        {id:'e2',definition_id:'d2',variant_id:'v2',label:'Second',operation:'transport',point_ids:['p3'],voltage_mode:'independent'}],
      points:[{id:'p1',execution_id:'e1',branch:'T1',order:1,initialization:'cold',predecessor_id:null,temperature_K:70,voltage_per_period_V:0},
        {id:'p2',execution_id:'e1',branch:'T1',order:2,initialization:'previous',predecessor_id:'p1',temperature_K:70,voltage_per_period_V:0.05},
        {id:'p3',execution_id:'e2',branch:'T1',order:3,initialization:'cold',predecessor_id:null,temperature_K:70,voltage_per_period_V:0.1}]}};
}
const render=(value:unknown)=>renderToStaticMarkup(createElement(ui.RunStructure,{run:value}));
test('exact arrow, no implicit independent edge, two outcomes, planned unknown points and attempt links',()=>{
  const html=render(run());
  for(const literal of ['p1 → p2','p3 (без predecessor)','child-1','child-2','excepted','finished','scientific: unknown','attempt не создан','fingerprint-A','v1','v2'])assert.ok(html.includes(literal),literal);
  assert.doesNotMatch(html,/p2 → p3|scientific: pass|<script>/);
  assert.ok(html.includes('&lt;script&gt;bad&lt;/script&gt;'));
  assert.ok(html.includes('href="#plan-run-A-execution-e1"'));assert.ok(html.includes('id="plan-run-A-attempt-child-2"'));
});
test('old/unavailable/malformed producer leaves all observed attempts readable',()=>{
  for(const structure of [undefined,{status:'unavailable',reason:'unsupported_plan_schema'},{status:'available',root:{}}]) {
    const value={...run(),plan_structure:structure}; const html=render(value);
    assert.ok(html.includes('Структура сохранённого плана недоступна'));assert.ok(html.includes('child-1'));assert.ok(html.includes('child-2'));
    assert.doesNotMatch(html,/p1 → p2/);
  }
});
test('101 points render first 100, total and accessible local navigation',()=>{
  const value=run();value.plan_structure.executions=value.plan_structure.executions.slice(0,1);
  value.plan_structure.inclusions[0].execution_ids=['e1'];
  value.plan_structure.points=Array.from({length:101},(_,i)=>({...value.plan_structure.points[0],id:`point-${i}`,order:i+1}));
  value.plan_structure.executions[0].point_ids=value.plan_structure.points.map(p=>p.id);
  const html=render(value);
  assert.ok(html.includes('Точек всего: 101'));assert.ok(html.includes('Страница 1 из 2'));
  assert.ok(html.includes('point-99 (без predecessor)'));assert.ok(!html.includes('point-100 (без predecessor)'));
  assert.ok(html.includes('aria-label="Страницы точек e1"'));assert.ok(html.includes('Следующие 100'));
});
test('new run rendering has no previous run IDs; inner identity resets pagination state',()=>{
  render(run());const html=render({...run('run-B'),children:[],plan_structure:undefined});
  assert.doesNotMatch(html,/run-A|child-1|p1 → p2/);assert.ok(html.includes('run-B'));
  assert.match(source,/<StructureDetail key=\{run.uuid\}/);
});
test('component contains only local display buttons and light focus styles',async()=>{
  assert.doesNotMatch(source,/submit_plan|kill_run|prepareExport|fetch\(|dangerouslySetInnerHTML|is_finished_ok/);
  assert.doesNotMatch(source,/<button(?![^>]*type="button")/);
  const css=await readFile(new URL('../src/style.css',import.meta.url),'utf8');
  assert.match(css,/\.run-structure \{ color-scheme: light; background: #fff/);
  assert.match(css,/\.run-structure a:focus-visible/);
});
