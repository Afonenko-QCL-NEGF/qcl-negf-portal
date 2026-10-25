/** Finite semantic Results transport. No scientific resolution or acceptance rules. */
export interface SavedProviderSource {provider:'aiida_retrieved'|'nfs_registered';source_uuid:string|null;source_sha256:string|null}
export const retrievedSource:SavedProviderSource={provider:'aiida_retrieved',source_uuid:null,source_sha256:null};
export interface NativePin {source_native_identity?:Record<string,unknown>|null;source_calcjob_uuid?:string|null}
export interface SavedSources {provider:'nfs_registered';sources:{status:string;source_uuid:string;source_sha256:string;metadata_status:string;copy_status:unknown;access_status:string}[];complete:true;storage_binding_status:string}
export interface SavedNativeSelection {run_uuid:string|null;execution_id:string;attempt:number;calcjob_uuid:string|null}
export interface SavedSelection {run_uuid:string;execution_id:string;attempt:number;calcjob_uuid:string}
export interface SavedPoint extends NativePin {analysis_source_status?:string;point_id:string;coordinates:Record<string,unknown>;source_commit_sha256:string|null;source_attempt:number|null;scalar_observables:Record<string,unknown>;recorded_statuses:Record<string,unknown>;missing:string[]}
export interface SavedBrief {provider_source:SavedProviderSource;snapshot_complete?:boolean;registered_point_ids?:string[];storage_binding_status?:string;schema:'qcl-negf.saved-results.v1';provider_selection:SavedSelection;selection:SavedSelection;metadata_sha256:string;points:SavedPoint[];current_segments:string[][];total:number;next_cursor:string|null;complete_page:boolean;identity:Record<string,unknown>;statuses:Record<string,unknown>;source:Record<string,unknown>}
export interface SavedView {view_id:string;availability:string;reason:string|null;shape?:number[];axes?:string[];units?:string;basis?:string|null;conventions?:Record<string,unknown>;allowed_selection?:{axis_count:number}}
export interface SavedManifest extends NativePin {provider_source:SavedProviderSource;provider_selection:SavedSelection;metadata_sha256:string;source_commit_sha256:string;point_id:string;source_attempt:number;views:SavedView[];files:Record<string,unknown>[];integrity:Record<string,unknown>}
export type Numbers = number|null|Numbers[];
export type Masks = string|null|Masks[];
export interface SavedAxis {name:string;values:number[];units:string;source_path?:string;weights?:number[]|null;integration_measure?:string|null;physical_measure_multiplier?:number|null;physical_measure_units?:string|null}
export interface SavedSlice extends NativePin {provider_source:SavedProviderSource;provider_selection:SavedSelection;selection:SavedNativeSelection;metadata_sha256:string;point_id:string;source_attempt:number;source_commit_sha256:string;artifact_sha256:string;view_id:string;returned_shape:number[];logical_axes:string[];axes:SavedAxis[];values:Numbers;invalid_mask:Masks;units:string;basis?:string|null;conventions:Record<string,unknown>;recorded_statuses:Record<string,unknown>;fractions?:Record<string,unknown>|null;integrity?:Record<string,unknown>;source?:Record<string,unknown>}
type Row=Record<string,unknown>;
const object=(v:unknown):v is Row=>!!v&&typeof v==='object'&&!Array.isArray(v);
const hash=(v:unknown):v is string=>typeof v==='string'&&/^[0-9a-f]{64}$/.test(v);
const finite=(v:unknown):v is number=>typeof v==='number'&&Number.isFinite(v);
const count=(v:unknown):v is number=>Number.isSafeInteger(v)&&Number(v)>=0;
function require(v:unknown):asserts v {if(!v)throw new Error('Unsupported saved-result response; refresh exact selection');}
function bounded(value:unknown) {
 let nodes=0;const todo:[unknown,number][]=[[value,0]];
 while(todo.length){const [v,depth]=todo.pop()!;require(++nodes<=300000&&depth<=64);
  if(typeof v==='number')require(Number.isFinite(v));
  else if(Array.isArray(v))todo.push(...v.map(x=>[x,depth+1] as [unknown,number]));
  else if(object(v))todo.push(...Object.values(v).map(x=>[x,depth+1] as [unknown,number]));
  else require(v===null||typeof v==='string'||typeof v==='boolean');
 }
}
function sameValue(a:unknown,b:unknown):boolean {if(object(a)&&object(b)){const keys=Object.keys(a).sort();return keys.join('\0')===Object.keys(b).sort().join('\0')&&keys.every(k=>sameValue(a[k],b[k]));}if(Array.isArray(a)&&Array.isArray(b))return a.length===b.length&&a.every((v,i)=>sameValue(v,b[i]));return a===b;}
function checkSource(value:unknown,source:SavedProviderSource){require(object(value)&&sameValue(value.provider_source,source));}
function checkNative(value:Row,pin:NativePin|undefined){require(pin&&object(value.source_native_identity)&&sameValue(value.source_native_identity,pin.source_native_identity)&&value.source_calcjob_uuid===pin.source_calcjob_uuid&&value.source_attempt===value.source_native_identity.attempt);const native=value.source_native_identity;require(typeof native.execution_id==='string'&&count(native.attempt)&&Number(native.attempt)>0&&(native.run_uuid==null||typeof native.run_uuid==='string')&&(native.calcjob_uuid==null||typeof native.calcjob_uuid==='string'));}
export function readSources(value:unknown,expected:SavedSelection):SavedSources {
 bounded(value);require(object(value)&&value.provider==='nfs_registered'&&sameSelection(value.selection,expected)&&sameSelection(value.provider_selection,expected)&&value.complete===true&&['configured','unavailable'].includes(String(value.storage_binding_status))&&Array.isArray(value.sources)&&value.sources.length<=256);
 const seen=new Set();for(const row of value.sources){require(object(row)&&sameSelection(row.selection,expected)&&row.status==='available'&&typeof row.source_uuid==='string'&&/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(row.source_uuid)&&hash(row.source_sha256)&&row.metadata_status==='verified'&&row.access_status==='not_checked'&&'copy_status' in row&&!seen.has(row.source_uuid));seen.add(row.source_uuid);}
 return value as unknown as SavedSources;
}
export function sameSelection(a:unknown,b:SavedSelection|SavedNativeSelection):boolean {return object(a)&&['run_uuid','execution_id','attempt','calcjob_uuid'].every(k=>a[k]===b[k as keyof SavedSelection]);}
export function readBrief(value:unknown,expected:SavedSelection,source:SavedProviderSource=retrievedSource):SavedBrief {
 bounded(value);checkSource(value,source);require(object(value)&&value.schema==='qcl-negf.saved-results.v1'&&sameSelection(value.provider_selection,expected)&&sameSelection(value.selection,expected)&&hash(value.metadata_sha256));
 require(Array.isArray(value.points)&&value.points.length<=256&&Array.isArray(value.current_segments)&&count(value.total)&&typeof value.complete_page==='boolean'&&(value.next_cursor===null||typeof value.next_cursor==='string')&&object(value.identity)&&object(value.statuses)&&object(value.source));
 for(const p of value.points)require(object(p)&&typeof p.point_id==='string'&&object(p.coordinates)&&object(p.scalar_observables)&&object(p.recorded_statuses)&&Array.isArray(p.missing)&&(p.source_commit_sha256===null||hash(p.source_commit_sha256)));
 if(source.provider==='nfs_registered'){
  require(typeof value.snapshot_complete==='boolean'&&Array.isArray(value.registered_point_ids));
  for(const p of value.points){
   require(object(p)&&['registered','unregistered','owner_unavailable'].includes(String(p.analysis_source_status)));
   if(p.analysis_source_status==='unregistered')require(p.source_commit_sha256===null&&p.source_native_identity===null&&p.source_calcjob_uuid===null);
   else {require(object(p.source_native_identity)&&p.source_native_identity.point_id===p.point_id&&p.source_native_identity.execution_id===expected.execution_id&&p.source_native_identity.attempt===p.source_attempt&&count(p.source_attempt)&&Number(p.source_attempt)>0&&Number(p.source_attempt)<=expected.attempt&&p.source_native_identity.plan_fingerprint===value.identity.plan_fingerprint&&(p.source_calcjob_uuid===null||typeof p.source_calcjob_uuid==='string'));}
  }
 }
 const ids=new Set(value.points.map(p=>(p as Row).point_id));require(ids.size===value.points.length);
 for(const segment of value.current_segments){require(Array.isArray(segment)&&segment.every(id=>ids.has(id)));for(const id of segment){const point=value.points.find(p=>(p as Row).point_id===id) as Row;require(object(point.coordinates)&&object(point.scalar_observables)&&finite(point.coordinates.voltage_per_period_V)&&finite(point.scalar_observables.current_density_A_per_m2));}}
 return value as unknown as SavedBrief;
}
export function readManifest(value:unknown,expected:SavedSelection,point:string,commit:string,metadata:string,source:SavedProviderSource=retrievedSource,pin?:NativePin):SavedManifest {
 bounded(value);checkSource(value,source);require(object(value)&&sameSelection(value.provider_selection,expected)&&value.point_id===point&&value.source_commit_sha256===commit&&value.metadata_sha256===metadata&&Array.isArray(value.views)&&value.views.length<=256&&Array.isArray(value.files)&&object(value.integrity));
 if(source.provider==='nfs_registered')checkNative(value,pin);
 for(const view of value.views)require(object(view)&&typeof view.view_id==='string'&&['available','unavailable'].includes(String(view.availability))&&(view.availability!=='available'||(Array.isArray(view.shape)&&view.shape.every(count)&&Array.isArray(view.axes)&&typeof view.units==='string'&&object(view.allowed_selection)&&count(view.allowed_selection.axis_count))));
 return value as unknown as SavedManifest;
}
export function readSlice(value:unknown,expected:SavedSelection,point:string,commit:string,view:string,metadata?:string,source:SavedProviderSource=retrievedSource,pin?:NativePin):SavedSlice {
 bounded(value);checkSource(value,source);require(object(value)&&sameSelection(value.provider_selection,expected)&&value.point_id===point&&value.source_commit_sha256===commit&&value.view_id===view&&hash(value.artifact_sha256)&&hash(value.metadata_sha256)&&(metadata===undefined||value.metadata_sha256===metadata));
 require(count(value.source_attempt)&&Number(value.source_attempt)>0&&Number(value.source_attempt)<=expected.attempt);
 if(source.provider==='nfs_registered'){
  checkNative(value,pin);const native=value.source_native_identity as Row;
  require(sameSelection(value.selection,{run_uuid:(native.run_uuid as string|null|undefined)??null,execution_id:native.execution_id as string,attempt:native.attempt as number,calcjob_uuid:(native.calcjob_uuid as string|null|undefined)??null}));
 }else require(sameSelection(value.selection,{...expected,attempt:Number(value.source_attempt)}));
 require(Array.isArray(value.returned_shape)&&value.returned_shape.length<=2&&value.returned_shape.every(count)&&Array.isArray(value.logical_axes)&&value.logical_axes.length===value.returned_shape.length&&Array.isArray(value.axes)&&typeof value.units==='string'&&object(value.conventions)&&object(value.recorded_statuses));
 let cells=1;for(const n of value.returned_shape)cells*=Number(n);require(cells<=65536);
 for(const axis of value.axes)require(object(axis)&&typeof axis.name==='string'&&typeof axis.units==='string'&&Array.isArray(axis.values)&&axis.values.every(finite)&&(axis.weights==null||(Array.isArray(axis.weights)&&axis.weights.length===axis.values.length&&axis.weights.every(finite))));
 const logical=value.logical_axes,axes=value.axes,shape=value.returned_shape;
 for(let i=0;i<logical.length;i++){const axis=axes.find(a=>(a as Row).name===logical[i]);require(object(axis)&&Array.isArray(axis.values)&&axis.values.length===shape[i]);}
 const verify=(numbers:unknown,masks:unknown,shape:number[])=>{
  if(shape.length){require(Array.isArray(numbers)&&Array.isArray(masks)&&numbers.length===shape[0]&&masks.length===shape[0]);numbers.forEach((n,i)=>verify(n,masks[i],shape.slice(1)));}
  else require((finite(numbers)&&masks===null)||(numbers===null&&['nan','positive_infinity','negative_infinity'].includes(String(masks))));
 };verify(value.values,value.invalid_mask,value.returned_shape as number[]);
 return value as unknown as SavedSlice;
}
export function selectionOptions(run:{uuid:string;children?:{uuid:string;execution_id:string;attempt:number}[]}):SavedSelection[] {
 const out:SavedSelection[]=[];for(const c of run.children??[])if(c.execution_id&&Number.isSafeInteger(c.attempt)&&c.attempt>0&&!out.some(v=>v.calcjob_uuid===c.uuid))out.push({run_uuid:run.uuid,execution_id:c.execution_id,attempt:c.attempt,calcjob_uuid:c.uuid});return out;
}
export function currentPaths(brief:SavedBrief):number[][][] {
 const points=new Map(brief.points.map(p=>[p.point_id,p]));
 return brief.current_segments.map(segment=>segment.map(id=>{const p=points.get(id)!;const v=p.coordinates.voltage_per_period_V,j=p.scalar_observables.current_density_A_per_m2;require(finite(v)&&finite(j));return [v,j];}));
}
/** Display geometry only; scientific values/units/masks are never rewritten. */
export function displayScale(values:number[]) {
 const geometry=(ok:boolean)=>{if(!ok)throw new Error('Finite display geometry unavailable');};
 geometry(values.length>0&&values.every(finite));
 const minimum=Math.min(...values),maximum=Math.max(...values);
 const divisor=Math.max(Math.abs(minimum),Math.abs(maximum),Number.MIN_VALUE);
 const lo=minimum/divisor,hi=maximum/divisor,span=hi-lo,singleton=minimum===maximum;
 geometry(singleton||(Number.isFinite(span)&&span>0));
 return {minimum,maximum,position:(value:number)=>{
  geometry(finite(value)&&value>=minimum&&value<=maximum);
  const position=singleton ? 0.5 : (value/divisor-lo)/span;
  geometry(Number.isFinite(position));return position;
 }};
}
/** Midpoint display edges; refuse unrepresentable edges, retaining raw centers. */
export function cellEdges(values:number[]):number[] {
 const geometry=(ok:boolean)=>{if(!ok)throw new Error('Finite display geometry unavailable');};
 geometry(values.length>0&&values.every(finite)&&values.every((v,i)=>i===0||v>values[i-1]));
 let edges:number[];
 if(values.length===1)edges=[values[0]-.5,values[0]+.5];
 else {
  edges=[values[0]-(values[1]/2-values[0]/2)];
  for(let i=1;i<values.length;i++)edges.push(values[i-1]/2+values[i]/2);
  edges.push(values.at(-1)!+(values.at(-1)!/2-values.at(-2)!/2));
 }
 geometry(edges.every(finite)&&edges.every((v,i)=>i===0||v>edges[i-1]));
 return edges;
}
export class SavedSession {
 private serial=0;invalidate(){this.serial++;}begin(){return ++this.serial;}current(id:number){return id===this.serial;}
 async receive(id:number,load:()=>Promise<unknown>,success:(v:unknown)=>void,failure:(e:unknown)=>void){try{const v=await load();if(this.current(id))success(v);}catch(e){if(this.current(id))failure(e);}}
}
