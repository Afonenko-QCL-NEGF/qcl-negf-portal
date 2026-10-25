import assert from 'node:assert/strict';
import {test} from 'node:test';
import {readFile} from 'node:fs/promises';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import ts from 'typescript';

// The locked compiler lowers this display module only. No browser or new DOM stack.
const filename=new URL('../src/configurator.tsx',import.meta.url);
const source=await readFile(filename,'utf8');
let output=ts.transpileModule(source,{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText;
for(const specifier of ['react','react/jsx-runtime','./api.ts','./configurator-data.ts']){
  const resolved=specifier.startsWith('.')?new URL(specifier,filename).href:import.meta.resolve(specifier);
  output=output.replaceAll(`"${specifier}"`,JSON.stringify(resolved)).replaceAll(`'${specifier}'`,JSON.stringify(resolved));
}
const ui=await import(`data:text/javascript;base64,${Buffer.from(output).toString('base64')}`);
const hash='a'.repeat(64);
const preview={status:'available',catalog_source_id:'research-source',template:{name:'Template',study:'config/studies/example.yaml',study_sha256:hash,description:'Candidate, no acceptance',model:{name:'<script>unsafe</script>',path:'model.yaml',sha256:hash},protocol:{name:'Protocol',path:'protocol.yaml',version:'source:protocol.yaml',sha256:hash}},draft_sha256:hash,semantic_draft_sha256:hash,plan_sha256:hash,fingerprint:hash,scientific_fingerprint:hash,counts:{points:2,executions:1},variant_diffs:[{id:'grid-study',comparison_kind:'discretization',controlled_paths:['numerical.energy_nodes'],overrides:{numerical:{energy_nodes:512}}}],resolved_inputs:[{execution_id:'e1',configuration:{scattering:{lo:true,acoustic:true,impurity:true,interface_roughness:true,alloy:true,electron_electron:{mode:'none'}}},assigning_sources:{}}],effective_defaults:[{execution_id:'e1',sections:{physical:{used:{temperature:{value:70,unit:'K'},optional:null},origin:'owning_resolution_rule',rule_source:{symbol:'physical_loader',sha256:hash},input_assigning_histories:{}}},point_sections:[{point_id:'p2',sections:{physical:{used:{voltage:{value:0.05,unit:'V'}}}}}]}],default_rule_sources:{runner:{sha256:hash},core:[{path:'core_constructor.jl',sha256:hash}]},estimates:{estimated_peak_bytes:8192,minimum_output_bytes:4096},short_form:{lossless:false,reason:'authoritative_yaml_requires_preservation'},draft_yaml:'# canonical semantic\nkind: study\n',plan:JSON.stringify({inclusions:[{id:'i1'}],executions:[{id:'e1',variant_id:'grid-study',operation:'transport',policies:{voltage_mode:'strict'}}],points:[{id:'p1',predecessor_id:null},{id:'p2',predecessor_id:'p1'}]})};
test('production preview renders all point/default/source sections, escaped text and explicit scientific boundaries',()=>{
  const html=renderToStaticMarkup(createElement(ui.ConfiguratorPreview,{preview}));
  for(const literal of ['attempt ещё не создан','source:protocol.yaml','point_sections','p2','predecessor_id','grid-study','energy_nodes','512','scattering','alloy','electron_electron','none','owning_resolution_rule','physical_loader','core_constructor.jl','minimum_output_bytes','не admission','unknown'])assert.ok(html.includes(literal),literal);
  assert.ok(html.includes('&lt;script&gt;unsafe&lt;/script&gt;'));assert.doesNotMatch(html,/<script>|scientific: pass|attempt 1|observed attempt/);
});
test('missing fields stay unknown, absent optional values stay null and initial catalog load is read-only',()=>{
  const html=renderToStaticMarkup(createElement(ui.ConfiguratorPreview,{preview:{...preview,plan:' {\"budget\":9223372036854775807}\n',default_rule_sources:undefined}}));
  assert.ok(html.includes('unknown — данные отсутствуют'));assert.ok(html.includes('null'));assert.ok(html.includes('9223372036854775807'));assert.ok(!html.includes('9223372036854776000'));
  const initial=renderToStaticMarkup(createElement(ui.Configurator,{token:'secret',context:'resources-A',onPlan:()=>{throw new Error('No submit during render');}}));
  assert.ok(initial.includes('Чтение каталога'));assert.ok(initial.includes('Runtime version: <!-- -->unknown')||initial.includes('Runtime version: unknown'));assert.doesNotMatch(initial,/Bearer|secret/);
});
