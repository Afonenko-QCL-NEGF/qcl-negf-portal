import assert from 'node:assert/strict';
import {test} from 'node:test';
import {readFile} from 'node:fs/promises';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import ts from 'typescript';
import {readStructureEditor,inputsToDraft,changeLayer} from '../src/structure-editor-data.ts';
const filename=new URL('../src/structure-editor.tsx',import.meta.url);
const compiled=ts.transpileModule(await readFile(filename,'utf8'),{fileName:filename.pathname,reportDiagnostics:true,compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}});
assert.deepEqual((compiled.diagnostics??[]).filter(d=>d.category===ts.DiagnosticCategory.Error).map(d=>ts.flattenDiagnosticMessageText(d.messageText,'\n')),[]);
let output=compiled.outputText;
for(const specifier of ['react','react/jsx-runtime','./structure-editor-data.ts']){const target=specifier.startsWith('.')?new URL(specifier,filename).href:import.meta.resolve(specifier);output=output.replaceAll(`"${specifier}"`,JSON.stringify(target)).replaceAll(`'${specifier}'`,JSON.stringify(target));}
const ui=await import(`data:text/javascript;base64,${Buffer.from(output).toString('base64')}`);
const hash='a'.repeat(64),source={catalog_source_id:'fixture',template_id:'tiny',template_sha256:hash,model:{name:'Model',path:'model.yaml',sha256:hash}};
const binding={...source,execution_id:'e1',variant_id:'baseline',execution_provenance:{sources:{'physical.layers':['authored:tiny']}},default_rule_sources:{'Runner:src/infrastructure/config/configuration.jl':hash},physical_rule_source:`Runner:src/infrastructure/config/configuration.jl@sha256:${hash}#_parse_physical`,input_assigning_histories:{'physical.layers':['authored:tiny']}};
const inputs={layers:[{thickness_nm:2,material:'<script>barrier</script>',aluminium_fraction:.25,conduction_band_edge_eV:.2,longitudinal_relative_mass:.09,transverse_relative_mass:.09,relative_permittivity:12.9,doped:false},{thickness_nm:3,material:'GaAs',aluminium_fraction:0,conduction_band_edge_eV:0,longitudinal_relative_mass:.067,transverse_relative_mass:.067,relative_permittivity:12.9,doped:true}],interfaces_nm:[0,2],donor_sheet_density_per_m2:1e14,ionization_fraction:.5,position_origin_nm:0,reference_energy_eV:0};
const dto=readStructureEditor({schema:'qcl-negf.structure-editor.v1',status:'available',executions:[{execution_id:'e1',variant_id:'baseline',source_binding:binding,inputs,geometry:{period_nm:5,layers:[{index_1based:1,left_nm:0,right_nm:2},{index_1based:2,left_nm:2,right_nm:5}],coordinate_scope:'one structural period before position-origin/bias sampling'},material_presets:[{material:'<script>barrier</script>',aluminium_fraction:.25,conduction_band_edge_eV:.2,longitudinal_relative_mass:.09,transverse_relative_mass:.09,relative_permittivity:12.9,source_binding:binding},{material:'GaAs',aluminium_fraction:0,conduction_band_edge_eV:0,longitudinal_relative_mass:.067,transverse_relative_mass:.067,relative_permittivity:12.9,source_binding:binding}]}]},source);
function render(draft=inputsToDraft(inputs)){
 assert.equal(dto.status,'available');if(dto.status!=='available')throw new Error('fixture');
 return renderToStaticMarkup(createElement(ui.StructureEditor,{capability:dto,bound:{execution:dto.executions[0],draft,dirty:true},onSelect:()=>{throw new Error('No render-side action');},onEdit:()=>{throw new Error('No render-side action');}}));
}
test('actual controls/geometry stay light, escaped, explicitly draft; no material or donor law',()=>{
 const html=render();
 for(const literal of ['Runner ещё не проверил','5 nm','Doped mask','m_parallel/m_e','epsilon_r','100000000000000','unknown/research origin','physical.layers','authored:tiny','интерполяции','Явные interfaces','не заменяет их границами'])assert.ok(html.includes(literal),literal);
 assert.match(html,/x="50" y="30" width="232"/);assert.match(html,/x="282" y="30" width="348"/);
 assert.ok(html.includes('&lt;script&gt;barrier&lt;/script&gt;'));assert.doesNotMatch(html,/<script>|NaN|Infinity|solve accepted|volume density=/);
 for(const match of html.matchAll(/\b(?:x|y|x1|x2|width|height)="([^"]+)"/g))assert.ok(Number.isFinite(Number(match[1])));
});
test('reordered draft keeps interfaces, warns outside period; incomplete numbers retain controls',()=>{
 let draft=changeLayer(inputsToDraft(inputs),1,'up');draft.interfaces_nm=['0','9'];
 const html=render(draft);assert.match(html,/width="348"/);assert.match(html,/вне периода interfaces/);assert.match(html,/0\n9/);
 draft.layers[0].thickness_nm='';const incomplete=render(draft);assert.match(incomplete,/Исходные controls сохранены/);assert.match(incomplete,/Толщина/);
});
test('missing/additive unavailable capability preserves existing YAML/plan path and never fabricates presets',()=>{
 const html=renderToStaticMarkup(createElement(ui.StructureEditor,{capability:{schema:'qcl-negf.structure-editor.v1',status:'unavailable',reason:'unsupported_structure_editor'},bound:null,onSelect:()=>{},onEdit:()=>{}}));
 assert.match(html,/unsupported_structure_editor/);assert.match(html,/YAML/);assert.doesNotMatch(html,/<svg|<input|GaAs/);
});
