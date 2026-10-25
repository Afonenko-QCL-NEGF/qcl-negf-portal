import assert from 'node:assert/strict';
import {test} from 'node:test';
import {readFile} from 'node:fs/promises';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import ts from 'typescript';
const filename=new URL('../src/research-cards.tsx',import.meta.url);
const source=await readFile(filename,'utf8');
let output=ts.transpileModule(source,{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText;
for(const specifier of ['react','react/jsx-runtime','./api','./research-card-data']) {
  const resolved=specifier.startsWith('.')?new URL(`${specifier}.ts`,filename).href:import.meta.resolve(specifier);
  output=output.replaceAll(`"${specifier}"`,JSON.stringify(resolved)).replaceAll(`'${specifier}'`,JSON.stringify(resolved));
}
const ui=await import(`data:text/javascript;base64,${Buffer.from(output).toString('base64')}`);
const C='11111111-1111-4111-8111-111111111111';
test('initial light controls are labelled and rendering does not dispatch any API calls',()=>{
  const html=renderToStaticMarkup(createElement(ui.ResearchCards,{token:'secret',onSelectRun:()=>{throw Error('unexpected navigation');}}));
  for(const literal of ['Исследования','Новая карточка','Название','Цель','Вопрос','Создать карточку','Назад','Далее','с карточкой не связан'])assert.ok(html.includes(literal),literal);
  assert.doesNotMatch(html,/secret|scientific: pass|type="password"/);
});
test('canonical question and author snapshot are escaped, visibly separate and version-pinned',()=>{
  const canonical=renderToStaticMarkup(createElement(ui.CanonicalCard,{card:{schema:'qcl-negf-research-card-v1',title:'T',goal:' Compare A/B ',question:'<script>canonical</script>\n**Markdown source**'},version:{version_uuid:C}}));
  assert.ok(canonical.includes(C));assert.ok(canonical.includes('&lt;script&gt;canonical&lt;/script&gt;'));assert.ok(canonical.includes('**Markdown source**'));
  assert.doesNotMatch(canonical,/<script>|<strong>Markdown/);
  const author=renderToStaticMarkup(createElement(ui.ResearchCardReport,{report:{question_snapshot:'<img src=x onerror=bad>',conclusion:'Negative result',reasoning:'<script>bad</script>',limitations:'No science acceptance',used_runs:[{run_uuid:C,attempt:2,variant_id:'mesh-refined'}]}}));
  for(const literal of ['author question_snapshot','Negative result','No science acceptance','mesh-refined','&lt;script&gt;bad&lt;/script&gt;'])assert.ok(author.includes(literal),literal);
  assert.doesNotMatch(author,/<img|<script>|scientific: pass/);
});
