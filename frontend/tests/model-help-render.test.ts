import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFile } from 'node:fs/promises';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import ts from 'typescript';

// Node strip-types does not lower TSX. Use the already locked compiler only for
// rendering this component module; no browser, bundle or new DOM dependency.
const filename=new URL('../src/model-help.tsx',import.meta.url);
const source=await readFile(filename,'utf8');
let output=ts.transpileModule(source,{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText;
for(const specifier of ['react','react/jsx-runtime','./api.ts','./model-help-data.ts']) {
  const resolved=specifier.startsWith('.')?new URL(specifier,filename).href:import.meta.resolve(specifier);
  output=output.replaceAll(`"${specifier}"`,JSON.stringify(resolved)).replaceAll(`'${specifier}'`,JSON.stringify(resolved));
}
const ui=await import(`data:text/javascript;base64,${Buffer.from(output).toString('base64')}`);
const context={run_uuid:'run-A',calcjob_uuid:'child-A',point_id:'point-A',execution_id:'execution-A',attempt:2,plan_fingerprint:'fp-A',scientific_fingerprint:'science-A',configuration_hash:'config-A',model_revision:'<script>bad</script>'};
test('unknown context is escaped and contains no invented scalar values',()=>{
  const html=renderToStaticMarkup(createElement(ui.UnknownContext,{view:{context,binding:{release_id:'recorded-A'}}}));
  assert.match(html,/Привязка источников недоступна/);assert.match(html,/Данные declared\/used недоступны/);
  assert.match(html,/recorded-A/);assert.match(html,/&lt;script&gt;bad&lt;\/script&gt;/);assert.doesNotMatch(html,/<script>/);
});
test('no automatic point/child selection and read initially disabled',()=>{
  const html=renderToStaticMarkup(createElement(ui.RunModelHelp,{token:'token',run:{uuid:'run-A',children:[],plan_points:[]}}));
  assert.match(html,/Контекст выбранного run/);assert.doesNotMatch(html,/Чтение контекста/);
});
test('G1 reference is version-labelled with three independent chapter journeys',()=>{
  const rev='b'.repeat(40),url=`/docs/${rev}/`;
  const html=renderToStaticMarkup(createElement(ui.PublishedReference,{documentation:{status:'available',source_revision:rev,bundle_sha256:'c'.repeat(64),url,journeys:{researcher:`${url}core/a.html#r`,developer:`${url}runner/a.html#d`,operator:`${url}operator/a.html#o`}}}));
  assert.ok(html.includes(rev));for(const text of ['Исследователю','Разработчику','Оператору'])assert.ok(html.includes(text));
  assert.doesNotMatch(html,/\/docs\/current/);
});
test('help actions are type=button and ordinary submit handlers remain isolated',()=>{
  assert.doesNotMatch(source,/submit_plan|kill_run|prepareExport|dangerouslySetInnerHTML|iframe/);
  assert.doesNotMatch(source,/<button(?![^>]*type="button")/);
});
