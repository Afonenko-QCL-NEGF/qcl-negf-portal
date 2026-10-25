import assert from 'node:assert/strict';
import {test} from 'node:test';
import {readStructureEditor,activeVariant,makeStructureVariant,inputsToDraft,draftToInputs,draftGeometry,changeLayer,applyPreset,StructureDraftStore} from '../src/structure-editor-data.ts';
const hash='a'.repeat(64),source={catalog_source_id:'source',template_id:'tiny',template_sha256:hash,model:{name:'Tiny',path:'model/tiny.yaml',sha256:hash}};
const binding={...source,execution_id:'e1',variant_id:'baseline',execution_provenance:{sources:{'physical.layers':['source:model/tiny.yaml']}},default_rule_sources:{'Runner:src/infrastructure/config/configuration.jl':hash},physical_rule_source:`Runner:src/infrastructure/config/configuration.jl@sha256:${hash}#_parse_physical`,input_assigning_histories:{'physical.layers':['source:model/tiny.yaml']}};
const barrier={thickness_nm:2,material:'AlGaAs',aluminium_fraction:.25,conduction_band_edge_eV:.2,longitudinal_relative_mass:.09,transverse_relative_mass:.09,relative_permittivity:12.9,doped:false};
const well={thickness_nm:3,material:'GaAs',aluminium_fraction:0,conduction_band_edge_eV:0,longitudinal_relative_mass:.067,transverse_relative_mass:.067,relative_permittivity:12.9,doped:true};
const inputs={layers:[barrier,well],interfaces_nm:[0,2],donor_sheet_density_per_m2:1e14,ionization_fraction:.5,position_origin_nm:0,reference_energy_eV:0};
const barrierPreset={material:'AlGaAs',aluminium_fraction:.25,conduction_band_edge_eV:.2,longitudinal_relative_mass:.09,transverse_relative_mass:.09,relative_permittivity:12.9,source_binding:binding};
const preset={material:'GaAs',aluminium_fraction:0,conduction_band_edge_eV:0,longitudinal_relative_mass:.067,transverse_relative_mass:.067,relative_permittivity:12.9,source_binding:binding};
const execution={execution_id:'e1',variant_id:'baseline',source_binding:binding,inputs,geometry:{period_nm:5,layers:[{index_1based:1,left_nm:0,right_nm:2},{index_1based:2,left_nm:2,right_nm:5}],coordinate_scope:'one structural period before position-origin/bias sampling'},material_presets:[barrierPreset,preset]};
const dto={schema:'qcl-negf.structure-editor.v1',status:'available',executions:[execution]};

test('actual additive DTO binds model/template/execution/presets; old response remains capability unavailable',()=>{
 const parsed=readStructureEditor(dto,source);assert.equal(parsed.status,'available');
 assert.deepEqual(readStructureEditor(undefined,source),{schema:'qcl-negf.structure-editor.v1',status:'unavailable',reason:'capability_not_provided'});
 for(const patch of [{source_binding:{...binding,catalog_source_id:'foreign'}},{source_binding:{...binding,model:{...source.model,sha256:'b'.repeat(64)}}},{inputs:{...inputs,layers:[{...barrier,thickness_nm:Infinity}]}},{inputs:{...inputs,interfaces_nm:Array(257).fill(0)}},{inputs:{...inputs,layers:Array(257).fill(barrier)}},{inputs:{...inputs,layers:[{...barrier,material:'я'.repeat(129)}]}},{inputs:{...inputs,donor_sheet_density_per_m2:9007199254740992}},{material_presets:[barrierPreset,{...preset,source_binding:{...binding,variant_id:'foreign'}}]}])assert.throws(()=>readStructureEditor({...dto,executions:[{...execution,...patch}]},source));
 assert.throws(()=>readStructureEditor({...dto,executions:[execution,execution]},source));
 assert.throws(()=>readStructureEditor({...dto,executions:[{...execution,inputs:{...inputs,extra:'unknown'}}]},source));
});
test('literal geometry reorder/duplicate/remove leaves explicit interfaces untouched; preset copies complete tuple',()=>{
 let draft=inputsToDraft(inputs);
 assert.deepEqual(draftGeometry(draft),{period_nm:5,layers:[{index_1based:1,left_nm:0,right_nm:2},{index_1based:2,left_nm:2,right_nm:5}]});
 draft=changeLayer(draft,1,'up');assert.deepEqual(draftGeometry(draft),{period_nm:5,layers:[{index_1based:1,left_nm:0,right_nm:3},{index_1based:2,left_nm:3,right_nm:5}]});
 assert.deepEqual(draft.interfaces_nm,['0','2']);draft.layers[0].thickness_nm='4';
 assert.deepEqual(draftGeometry(draft),{period_nm:6,layers:[{index_1based:1,left_nm:0,right_nm:4},{index_1based:2,left_nm:4,right_nm:6}]});
 draft=changeLayer(draft,0,'duplicate');assert.equal(draft.layers.length,3);draft=changeLayer(draft,0,'remove');assert.equal(draft.layers.length,2);
 const copied=applyPreset(draft,1,preset);assert.equal(copied.layers[1].thickness_nm,'2');assert.equal(copied.layers[1].doped,false);assert.equal(copied.layers[1].material,'GaAs');assert.equal(copied.layers[1].longitudinal_relative_mass,'0.067');assert.equal(copied.layers[1].conduction_band_edge_eV,'0');
 copied.layers[1].aluminium_fraction='0.7';assert.equal(copied.layers[1].conduction_band_edge_eV,'0');assert.equal(copied.layers[1].longitudinal_relative_mass,'0.067');
 assert.deepEqual(inputs.layers,[barrier,well]);
});
test('existing physical_parameter grammar contains exactly six whole-leaf paths; no volume density or numerical override',()=>{
 const variant=makeStructureVariant('structure-b',{...inputs,donor_sheet_density_per_m2:2e14});
 assert.deepEqual(variant,{id:'structure-b',comparison_kind:'physical_parameter',controlled_paths:['physical.layers','physical.interfaces','physical.donor_sheet_density','physical.ionization_fraction','physical.position_origin','physical.reference_energy'],overrides:{physical:{layers:[{thickness:'2 nm',material:'AlGaAs',aluminium_fraction:.25,conduction_band_edge:'0.2 eV',longitudinal_relative_mass:.09,transverse_relative_mass:.09,relative_permittivity:12.9,doped:false},{thickness:'3 nm',material:'GaAs',aluminium_fraction:0,conduction_band_edge:'0 eV',longitudinal_relative_mass:.067,transverse_relative_mass:.067,relative_permittivity:12.9,doped:true}],interfaces:['0 nm','2 nm'],donor_sheet_density:'200000000000000 m^-2',ionization_fraction:.5,position_origin:'0 nm',reference_energy:'0 eV'}}});
 assert.throws(()=>makeStructureVariant('baseline',inputs));assert.throws(()=>makeStructureVariant('occupied',inputs,['occupied']));
 const draft=inputsToDraft(inputs);draft.layers[0].thickness_nm='';assert.throws(()=>draftToInputs(draft));draft.layers[0].thickness_nm='Infinity';assert.throws(()=>draftToInputs(draft));
 assert.throws(()=>makeStructureVariant('x',{...inputs,ionization_fraction:NaN}));
});
test('draft survives refresh/editor switching but token/template/model context clears binding synchronously',()=>{
 const capability=readStructureEditor(dto,source);assert.equal(capability.status,'available');if(capability.status!=='available')return;
 const store=new StructureDraftStore();store.setContext('token-A/template-A/model-A');store.offer(capability);assert.equal(store.bound,null);
 store.select('e1');const draft=inputsToDraft(inputs);draft.layers[0].thickness_nm='7';store.edit(draft);
 store.offer(capability);assert.equal(store.bound?.draft.layers[0].thickness_nm,'7');assert.equal(store.bound?.dirty,true);
 // Switching numerical/structure uses the same store, no refresh reinitialization.
 store.setContext('token-A/template-A/model-A');assert.equal(store.bound?.draft.layers[0].thickness_nm,'7');
 store.setContext('token-A/template-B/model-B');assert.equal(store.bound,null);assert.equal(store.latest,null);
 store.offer(capability);store.select('e1');store.setContext('token-B/template-B/model-B');assert.equal(store.bound,null);
});

test('exactly selected variant is evaluated; numerical/structure buffers and raw YAML are preserved',async()=>{
 const {makeRequest}=await import('../src/configurator-data.ts');
 const numerical={id:'grid',overrides:{numerical:{energy_nodes:101}}};
 const physical=makeStructureVariant('physical',inputs);let calls=0;
 assert.equal(activeVariant('none',()=>{throw new Error('unselected');},()=>{throw new Error('unselected');}),undefined);
 assert.equal(activeVariant('numerical',()=>{calls++;return numerical;},()=>{throw new Error('incomplete structure buffer');}),numerical);
 assert.equal(activeVariant('structure',()=>{throw new Error('incomplete numerical buffer');},()=>{calls++;return physical;}),physical);
 assert.equal(calls,2);assert.deepEqual(numerical.overrides.numerical,{energy_nodes:101});
 const raw='# exact expert YAML\nconfiguration:\n  overrides: {physical: {unknown: 1}}\n';
 assert.equal(makeRequest('yaml','tiny','source',{variant:physical},raw).yaml,raw);
 assert.deepEqual(makeRequest('form','tiny','source',{variant:physical},raw).fields,{variant:physical});
});

test('presets equal complete deduplicated full input-layer tuple set, even with a matching source binding',()=>{
 for(const presets of [[barrierPreset,{...preset,conduction_band_edge_eV:7}],[barrierPreset,preset,preset],[barrierPreset],[]]){
  assert.throws(()=>readStructureEditor({...dto,executions:[{...execution,material_presets:presets}]},source));
 }
 const repeated={...execution,inputs:{...inputs,layers:[barrier,well,{...well,thickness_nm:4,doped:false}]},geometry:{period_nm:9,layers:[{index_1based:1,left_nm:0,right_nm:2},{index_1based:2,left_nm:2,right_nm:5},{index_1based:3,left_nm:5,right_nm:9}],coordinate_scope:'one structural period before position-origin/bias sampling'},material_presets:[barrierPreset,preset]};
 const result=readStructureEditor({...dto,executions:[repeated]},source);
 assert.equal(result.status,'available');if(result.status!=='available')return;
 assert.deepEqual(result.executions[0].material_presets,[barrierPreset,preset]);
 assert.equal(result.executions[0].inputs.layers.length,3);
});

test('producer signed-zero material tuples remain two exact presets',()=>{
 const signed={...execution,inputs:{...inputs,layers:[{...well,conduction_band_edge_eV:-0},{...well,conduction_band_edge_eV:0}]},geometry:{period_nm:6,layers:[{index_1based:1,left_nm:0,right_nm:3},{index_1based:2,left_nm:3,right_nm:6}],coordinate_scope:'one structural period before position-origin/bias sampling'},material_presets:[{...preset,conduction_band_edge_eV:-0},{...preset,conduction_band_edge_eV:0}]};
 const accepted=readStructureEditor({...dto,executions:[signed]},source);
 assert.equal(accepted.status,'available');if(accepted.status!=='available')return;
 assert.equal(accepted.executions[0].material_presets.length,2);
 assert.ok(Object.is(accepted.executions[0].inputs.layers[0].conduction_band_edge_eV,-0));
 assert.ok(Object.is(accepted.executions[0].inputs.layers[1].conduction_band_edge_eV,0));
 assert.ok(Object.is(accepted.executions[0].material_presets[0].conduction_band_edge_eV,-0));
 assert.ok(Object.is(accepted.executions[0].material_presets[1].conduction_band_edge_eV,0));
 assert.throws(()=>readStructureEditor({...dto,executions:[{...signed,material_presets:[{...preset,conduction_band_edge_eV:0}]}]},source));
});
