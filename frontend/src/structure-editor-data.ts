/** Canonical Runner quantities only. No YAML parser, material law, donor normalization or solve. */
export interface MaterialTuple {material:string;aluminium_fraction:number;conduction_band_edge_eV:number;longitudinal_relative_mass:number;transverse_relative_mass:number;relative_permittivity:number}
export interface StructureLayer extends MaterialTuple {thickness_nm:number;doped:boolean}
export interface StructureInputs {layers:StructureLayer[];interfaces_nm:number[];donor_sheet_density_per_m2:number;ionization_fraction:number;position_origin_nm:number;reference_energy_eV:number}
export interface StructureSource {catalog_source_id:string;template_id:string;template_sha256:string;model:{name:string;path:string;sha256:string}}
export interface StructureBinding extends StructureSource {execution_id:string;variant_id:string;execution_provenance:{sources:Record<string,string[]>};default_rule_sources:Record<string,string>;physical_rule_source:string;input_assigning_histories:Record<string,string[]>}
export interface MaterialPreset extends MaterialTuple {source_binding:StructureBinding}
export interface LayerGeometry {index_1based:number;left_nm:number;right_nm:number}
export interface StructureExecution {execution_id:string;variant_id:string;inputs:StructureInputs;geometry:{period_nm:number;layers:LayerGeometry[];coordinate_scope:string};material_presets:MaterialPreset[];source_binding:StructureBinding}
export type StructureCapability={schema:'qcl-negf.structure-editor.v1';status:'available';executions:StructureExecution[]}|{schema:'qcl-negf.structure-editor.v1';status:'unavailable';reason:string};
export type AvailableStructure=Extract<StructureCapability,{status:'available'}>;
export type MaterialDraft={material:string;aluminium_fraction:string;conduction_band_edge_eV:string;longitudinal_relative_mass:string;transverse_relative_mass:string;relative_permittivity:string};
export interface LayerDraft extends MaterialDraft {thickness_nm:string;doped:boolean}
export interface StructureDraft {layers:LayerDraft[];interfaces_nm:string[];donor_sheet_density_per_m2:string;ionization_fraction:string;position_origin_nm:string;reference_energy_eV:string}
export interface BoundStructureDraft {execution:StructureExecution;draft:StructureDraft;dirty:boolean}
type Row=Record<string,unknown>;
const object=(v:unknown):v is Row=>!!v&&typeof v==='object'&&!Array.isArray(v);
const scalar=(v:unknown):v is number=>typeof v==='number'&&Number.isFinite(v)&&Math.abs(v)<=Number.MAX_SAFE_INTEGER;
const hash=(v:unknown):v is string=>typeof v==='string'&&/^[0-9a-f]{64}$/.test(v);
const text=(v:unknown):v is string=>typeof v==='string'&&v.length>0;
const materialKeys=['material','aluminium_fraction','conduction_band_edge_eV','longitudinal_relative_mass','transverse_relative_mass','relative_permittivity'] as const;
const inputKeys=['layers','interfaces_nm','donor_sheet_density_per_m2','ionization_fraction','position_origin_nm','reference_energy_eV'] as const;
function require(ok:unknown):asserts ok {if(!ok)throw new Error('Некорректный structure_editor DTO или source binding');}
function keys(row:Row,expected:readonly string[]){require(Object.keys(row).sort().join('\0')===[...expected].sort().join('\0'));}
function finiteBounded(value:unknown){
 const todo:[unknown,number][]=[[value,0]];let nodes=0;
 while(todo.length){const [v,depth]=todo.pop()!;require(++nodes<=100000&&depth<=64);
  if(typeof v==='number')require(scalar(v));else if(Array.isArray(v)){require(v.length<=100000);for(const item of v)todo.push([item,depth+1]);}
  else if(object(v)){for(const item of Object.values(v))todo.push([item,depth+1]);}
  else require(v===null||typeof v==='string'||typeof v==='boolean');
 }
 require(new TextEncoder().encode(JSON.stringify(value)).byteLength<=1048576);
}
function same(a:unknown,b:unknown):boolean {if(object(a)&&object(b)){const ka=Object.keys(a).sort();return ka.join('\0')===Object.keys(b).sort().join('\0')&&ka.every(k=>same(a[k],b[k]));}if(Array.isArray(a)&&Array.isArray(b))return a.length===b.length&&a.every((v,i)=>same(v,b[i]));return a===b;}
function histories(v:unknown){require(object(v));for(const items of Object.values(v))require(Array.isArray(items)&&items.every(text));}
function checkBinding(v:unknown,source:StructureSource,execution:string,variant:string):asserts v is StructureBinding {
 require(object(v));keys(v,['execution_id','variant_id','catalog_source_id','template_id','template_sha256','model','execution_provenance','default_rule_sources','physical_rule_source','input_assigning_histories']);
 require(v.execution_id===execution&&v.variant_id===variant&&v.catalog_source_id===source.catalog_source_id&&v.template_id===source.template_id&&v.template_sha256===source.template_sha256&&same(v.model,source.model));
 require(object(v.execution_provenance));keys(v.execution_provenance,['sources']);histories(v.execution_provenance.sources);histories(v.input_assigning_histories);
 require(object(v.default_rule_sources)&&Object.values(v.default_rule_sources).every(hash)&&text(v.physical_rule_source));
 const rule=v.default_rule_sources['Runner:src/infrastructure/config/configuration.jl'];
 require(hash(rule)&&v.physical_rule_source===`Runner:src/infrastructure/config/configuration.jl@sha256:${rule}#_parse_physical`);
}
function checkMaterial(v:unknown,layer:boolean,preset=false):asserts v is StructureLayer {
 require(object(v));keys(v,[...materialKeys,...(layer?['thickness_nm','doped']:[]),...(preset?['source_binding']:[])]);
 require(typeof v.material==='string'&&new TextEncoder().encode(v.material).byteLength<=256);
 for(const name of materialKeys)if(name!=='material')require(scalar(v[name]));
 if(layer)require(scalar(v.thickness_nm)&&typeof v.doped==='boolean');
}
export function checkInputs(value:unknown):asserts value is StructureInputs {
 require(object(value));keys(value,inputKeys);
 require(Array.isArray(value.layers)&&value.layers.length>=1&&value.layers.length<=256&&Array.isArray(value.interfaces_nm)&&value.interfaces_nm.length<=256&&value.interfaces_nm.every(scalar));
 for(const layer of value.layers)checkMaterial(layer,true);
 for(const name of inputKeys)if(name!=='layers'&&name!=='interfaces_nm')require(scalar(value[name]));
}
export function readStructureEditor(value:unknown,source:StructureSource):StructureCapability {
 if(value===undefined)return {schema:'qcl-negf.structure-editor.v1',status:'unavailable',reason:'capability_not_provided'};
 finiteBounded(value);require(object(value)&&value.schema==='qcl-negf.structure-editor.v1');
 if(value.status==='unavailable'){keys(value,['schema','status','reason']);require(text(value.reason));return value as unknown as StructureCapability;}
 keys(value,['schema','status','executions']);require(value.status==='available'&&Array.isArray(value.executions)&&value.executions.length>=1&&value.executions.length<=100);
 const ids=new Set();
 for(const execution of value.executions){
  require(object(execution));keys(execution,['execution_id','variant_id','inputs','geometry','material_presets','source_binding']);
  require(text(execution.execution_id)&&text(execution.variant_id)&&!ids.has(execution.execution_id));ids.add(execution.execution_id);
  checkBinding(execution.source_binding,source,execution.execution_id,execution.variant_id);checkInputs(execution.inputs);
  const geometry=execution.geometry;require(object(geometry));keys(geometry,['period_nm','layers','coordinate_scope']);
  require(scalar(geometry.period_nm)&&Array.isArray(geometry.layers)&&geometry.layers.length===execution.inputs.layers.length&&geometry.coordinate_scope==='one structural period before position-origin/bias sampling');
  for(let i=0;i<geometry.layers.length;i++){const layer=geometry.layers[i];require(object(layer));keys(layer,['index_1based','left_nm','right_nm']);require(layer.index_1based===i+1&&scalar(layer.left_nm)&&scalar(layer.right_nm)&&layer.right_nm>layer.left_nm&&(i===0?layer.left_nm===0:layer.left_nm===(geometry.layers[i-1] as Row).right_nm));}
  require(geometry.period_nm===(geometry.layers.at(-1) as Row).right_nm);
  require(Array.isArray(execution.material_presets)&&execution.material_presets.length<=256);
  const tupleEqual=(a:MaterialTuple,b:MaterialTuple)=>materialKeys.every(key=>Object.is(a[key],b[key]));
  const expected=execution.inputs.layers.filter((layer,index,layers)=>!layers.slice(0,index).some(previous=>tupleEqual(previous,layer)));
  const seen:MaterialTuple[]=[];
  for(const preset of execution.material_presets){
   checkMaterial(preset,false,true);require(object(preset)&&same(preset.source_binding,execution.source_binding));
   require(expected.some(layer=>tupleEqual(layer,preset))&&!seen.some(previous=>tupleEqual(previous,preset)));seen.push(preset);
  }
  require(seen.length===expected.length);
 }
 return value as unknown as StructureCapability;
}
export function inputsToDraft(inputs:StructureInputs):StructureDraft {
 checkInputs(inputs);
 return {layers:inputs.layers.map(layer=>({thickness_nm:String(layer.thickness_nm),material:layer.material,aluminium_fraction:String(layer.aluminium_fraction),conduction_band_edge_eV:String(layer.conduction_band_edge_eV),longitudinal_relative_mass:String(layer.longitudinal_relative_mass),transverse_relative_mass:String(layer.transverse_relative_mass),relative_permittivity:String(layer.relative_permittivity),doped:layer.doped})),interfaces_nm:inputs.interfaces_nm.map(String),donor_sheet_density_per_m2:String(inputs.donor_sheet_density_per_m2),ionization_fraction:String(inputs.ionization_fraction),position_origin_nm:String(inputs.position_origin_nm),reference_energy_eV:String(inputs.reference_energy_eV)};
}
export function draftNumber(raw:string):number {if(typeof raw!=='string'||!/^[-+]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][-+]?[0-9]+)?$/.test(raw)||!scalar(Number(raw)))throw new Error('Нужно явное конечное browser-safe число без единиц и пробелов');return Number(raw);}
export function draftToInputs(draft:StructureDraft):StructureInputs {
 const inputs={layers:draft.layers.map(layer=>({material:layer.material,doped:layer.doped,thickness_nm:draftNumber(layer.thickness_nm),aluminium_fraction:draftNumber(layer.aluminium_fraction),conduction_band_edge_eV:draftNumber(layer.conduction_band_edge_eV),longitudinal_relative_mass:draftNumber(layer.longitudinal_relative_mass),transverse_relative_mass:draftNumber(layer.transverse_relative_mass),relative_permittivity:draftNumber(layer.relative_permittivity)})),interfaces_nm:draft.interfaces_nm.map(draftNumber),donor_sheet_density_per_m2:draftNumber(draft.donor_sheet_density_per_m2),ionization_fraction:draftNumber(draft.ionization_fraction),position_origin_nm:draftNumber(draft.position_origin_nm),reference_energy_eV:draftNumber(draft.reference_energy_eV)};
 checkInputs(inputs);return inputs;
}
export function draftGeometry(draft:StructureDraft):{period_nm:number;layers:LayerGeometry[]} {
 if(!draft.layers.length||draft.layers.length>256)throw new Error('Для геометрии нужны 1–256 слоёв');
 let left=0;const layers=draft.layers.map((layer,i)=>{const width=draftNumber(layer.thickness_nm),right=left+width;if(width<=0||!scalar(right)||right<=left)throw new Error('Геометрия черновика недоступна: нужна положительная представимая толщина');const geometry={index_1based:i+1,left_nm:left,right_nm:right};left=right;return geometry;});return {period_nm:left,layers};
}
export function changeLayer(draft:StructureDraft,index:number,action:'up'|'down'|'duplicate'|'remove'|'add'):StructureDraft {
 const layers=draft.layers.map(layer=>({...layer}));if(index<0||index>=layers.length)throw new Error('Выберите слой');
 if(action==='remove'){if(layers.length===1)throw new Error('Нужен хотя бы один слой');layers.splice(index,1);}
 else if(action==='duplicate'||action==='add'){if(layers.length>=256)throw new Error('Не более 256 слоёв');layers.splice(index+1,0,{...layers[index]});}
 else {const other=action==='up'?index-1:index+1;if(other<0||other>=layers.length)throw new Error('Граница списка');[layers[index],layers[other]]=[layers[other],layers[index]];}
 return {...draft,layers,interfaces_nm:[...draft.interfaces_nm]};
}
export function applyPreset(draft:StructureDraft,index:number,preset:MaterialPreset):StructureDraft {
 checkMaterial(preset,false,true);const layers=draft.layers.map(layer=>({...layer}));if(!layers[index])throw new Error('Выберите слой');
 for(const key of materialKeys)layers[index][key]=String(preset[key]);
 return {...draft,layers,interfaces_nm:[...draft.interfaces_nm]};
}
export function makeStructureVariant(id:string,inputs:StructureInputs,existingIds:readonly string[]=['baseline']) {
 checkInputs(inputs);if(!/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(id)||existingIds.includes(id))throw new Error('Нужен новый неповторяющийся variant ID');
 return {id,comparison_kind:'physical_parameter',controlled_paths:['physical.layers','physical.interfaces','physical.donor_sheet_density','physical.ionization_fraction','physical.position_origin','physical.reference_energy'],overrides:{physical:{layers:inputs.layers.map(layer=>({thickness:`${layer.thickness_nm} nm`,material:layer.material,aluminium_fraction:layer.aluminium_fraction,conduction_band_edge:`${layer.conduction_band_edge_eV} eV`,longitudinal_relative_mass:layer.longitudinal_relative_mass,transverse_relative_mass:layer.transverse_relative_mass,relative_permittivity:layer.relative_permittivity,doped:layer.doped})),interfaces:inputs.interfaces_nm.map(v=>`${v} nm`),donor_sheet_density:`${inputs.donor_sheet_density_per_m2} m^-2`,ionization_fraction:inputs.ionization_fraction,position_origin:`${inputs.position_origin_nm} nm`,reference_energy:`${inputs.reference_energy_eV} eV`}}};
}
export class StructureDraftStore {
 private context='';revision=0;latest:StructureCapability|null=null;bound:BoundStructureDraft|null=null;
 setContext(key:string){if(key!==this.context){this.context=key;this.revision++;this.latest=null;this.bound=null;}}
 offer(value:StructureCapability){this.latest=value;} // Refresh never replaces an unsubmitted draft.
 select(executionId:string){const e=this.latest?.status==='available'?this.latest.executions.find(e=>e.execution_id===executionId):undefined;if(!e)throw new Error('Выберите execution актуального DTO');this.bound={execution:e,draft:inputsToDraft(e.inputs),dirty:false};}
 edit(draft:StructureDraft){if(!this.bound)throw new Error('Сначала выберите execution');this.bound={...this.bound,draft,dirty:true};}
 reset(){this.latest=null;this.bound=null;}
}
/** Lazy selection keeps unselected buffers intact, including incomplete expert draft text. */
export function activeVariant(editor:'none'|'numerical'|'structure',numerical:()=>unknown,structure:()=>unknown):unknown {
 return editor==='numerical'?numerical():editor==='structure'?structure():undefined;
}
