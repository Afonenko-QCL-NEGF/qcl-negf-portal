import type {Run} from './api.ts';
/** Native5 public selected-source transport; no scientific resolver. */
export interface SavedSelection {run_uuid:string;execution_id:string;attempt:number;calcjob_uuid:string}
export interface FileRef {path:string;bytes:string;sha256:string;schema:string;media_type:string}
export interface StateRef {identity:{run_id:string;execution_id:string;point_id:string;attempt:string;branch_id:string;model_sha256:string;plan_sha256:string};state_id:string;state_sequence:string;manifest:FileRef}
export interface NativeContext {selection:SavedSelection;point_id:string;native_binding_uuid:string;state_manifest_sha256:string;state_ref?:StateRef}
export interface NativeEnvelope<T=Record<string,unknown>> {schema:'qcl-negf.portal-selected-reader.v2';operation:string;provider_selection:SavedSelection;provider:'native_nfs';selection_mode:'retained';source_state:StateRef;retained:{native_result:Record<string,unknown>;native_binding:Record<string,unknown>;creator:unknown;integrity:{readback:{native_contexts:'not_reverified';current_access:'not_checked';scientific_acceptance:'not_assessed'};original_binding:{scope:'selected_metadata';native_contexts:'verified';transport_owner:'verified';full_core_payloads:'not_assessed';current_access:'not_checked'}}};access:Record<string,unknown>;view:T}
export interface SavedAxis {name:string;values:number[];units:string;weights?:number[]|null;[key:string]:unknown}
export interface SavedView {view_id:string;availability:'available'|'unavailable';shape?:number[]|null;axes?:string[];units:string;basis?:string|null;issue?:unknown;allowed_selection?:{axis_count:number};[key:string]:unknown}
export type Numbers=number|null|Numbers[];
export type Masks=string|null|Masks[];
export interface SavedSlice {source_state:StateRef;view_id:string;returned_shape:number[];logical_axes:string[];axes:SavedAxis[];values:Numbers;invalid_mask:Masks;units:string;basis?:string|null;conventions:Record<string,unknown>;recorded_statuses:Record<string,unknown>;fractions?:Record<string,unknown>|null;integrity?:Record<string,unknown>;source?:Record<string,unknown>}
export type SavedManifest=Record<string,unknown>&{views:Record<string,SavedView>};
export const object=(v:unknown):v is Record<string,unknown>=>!!v&&typeof v==='object'&&!Array.isArray(v);
export function requireNative(v:unknown):asserts v {if(!v)throw new Error('Источник или точный выбор изменился; обновите сведения выбранной точки');}
export function sameValue(a:unknown,b:unknown):boolean {if(object(a)&&object(b)){const keys=Object.keys(a).sort();return keys.join('\0')===Object.keys(b).sort().join('\0')&&keys.every(k=>sameValue(a[k],b[k]));}if(Array.isArray(a)&&Array.isArray(b))return a.length===b.length&&a.every((v,i)=>sameValue(v,b[i]));return a===b;}
const finite=(v:unknown):v is number=>typeof v==='number'&&Number.isFinite(v);
const hash=(v:unknown):v is string=>typeof v==='string'&&/^[a-f0-9]{64}(?![\s\S])/.test(v);
const uuid=(v:unknown):v is string=>typeof v==='string'&&/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}(?![\s\S])/.test(v);
const count=(v:unknown,positive=false):v is string=>typeof v==='string'&&/^(0|[1-9][0-9]{0,19})(?![\s\S])/.test(v)&&BigInt(v)<=18446744073709551615n&&(!positive||v!=='0');
export function boundedNative(value:unknown){let nodes=0;const todo:[unknown,number][]=[[value,0]];while(todo.length){const [v,d]=todo.pop()!;requireNative(++nodes<=300000&&d<=64);if(typeof v==='number')requireNative(Number.isFinite(v));else if(Array.isArray(v))for(const x of v)todo.push([x,d+1]);else if(object(v))for(const x of Object.values(v))todo.push([x,d+1]);else requireNative(v===null||typeof v==='boolean'||typeof v==='string');}requireNative(new TextEncoder().encode(JSON.stringify(value)).byteLength<=2097152);}
export function stateRef(value:unknown):asserts value is StateRef {
 requireNative(object(value)&&Object.keys(value).sort().join(',')==='identity,manifest,state_id,state_sequence'&&object(value.identity)&&object(value.manifest));
 const identity=value.identity,ref=value.manifest;
 requireNative(Object.keys(identity).sort().join(',')==='attempt,branch_id,execution_id,model_sha256,plan_sha256,point_id,run_id'&&['run_id','execution_id','point_id','branch_id'].every(k=>portable(identity[k]))&&count(identity.attempt,true)&&hash(identity.model_sha256)&&hash(identity.plan_sha256)&&count(value.state_sequence,true)&&portable(value.state_id));
 requireNative(Object.keys(ref).sort().join(',')==='bytes,media_type,path,schema,sha256'&&hash(ref.sha256)&&count(ref.bytes)&&typeof ref.path==='string'&&ref.path.length>0&&ref.path.length<=1024&&!/[\\\0:]/.test(ref.path)&&ref.path.split('/').every(p=>p!==''&&p!=='.'&&p!=='..')&&typeof ref.schema==='string'&&ref.schema.length>0&&typeof ref.media_type==='string'&&ref.media_type.length>0);
}
export interface PointSource {schema:'qcl-negf.portal-point-source.v1';status:'available';scope:'original_owner_metadata';provider_selection:SavedSelection;point:{point_id:string;point_attempt:number;plan_point_id:string};source:{kind:'absent'|'checkpoint'|'final';relation:'current_point_attempt'|'retained_source'|null;reason:string|null};source_state:StateRef|null;retained:NativeEnvelope['retained']}
const exactKeys=(v:Record<string,unknown>,keys:string)=>Object.keys(v).sort().join(',')===keys.split(',').sort().join(',');
const portable=(v:unknown)=>typeof v==='string'&&/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}(?![\s\S])/.test(v);
function retainedDTO(value:unknown):asserts value is NativeEnvelope['retained'] {
 requireNative(object(value)&&object(value.integrity));
 for(const key of ['native_result','native_binding']){const pin=value[key];requireNative(object(pin)&&exactKeys(pin,'node_uuid,bytes,sha256')&&uuid(pin.node_uuid)&&count(pin.bytes)&&hash(pin.sha256));}
 requireNative(sameValue(value.integrity.readback,{native_contexts:'not_reverified',current_access:'not_checked',scientific_acceptance:'not_assessed'})&&sameValue(value.integrity.original_binding,{scope:'selected_metadata',native_contexts:'verified',transport_owner:'verified',full_core_payloads:'not_assessed',current_access:'not_checked'}));
}
export function pointRequest(selection:SavedSelection,pointId:string){requireNative(exactKeys(selection as unknown as Record<string,unknown>,'run_uuid,execution_id,attempt,calcjob_uuid')&&uuid(selection.run_uuid)&&uuid(selection.calcjob_uuid)&&Number.isSafeInteger(selection.attempt)&&selection.attempt>0&&portable(selection.execution_id)&&portable(pointId));return {execution_id:selection.execution_id,attempt:selection.attempt,calcjob_uuid:selection.calcjob_uuid,point_id:pointId};}
export function readPointSource(value:unknown,selection:SavedSelection,pointId:string):PointSource {
 boundedNative(value);pointRequest(selection,pointId);
 requireNative(object(value)&&exactKeys(value,'schema,status,scope,provider_selection,point,source,source_state,retained')&&value.schema==='qcl-negf.portal-point-source.v1'&&value.status==='available'&&value.scope==='original_owner_metadata'&&sameValue(value.provider_selection,selection));
 requireNative(object(value.point)&&exactKeys(value.point,'point_id,point_attempt,plan_point_id')&&value.point.point_id===pointId&&value.point.plan_point_id===pointId&&Number.isSafeInteger(value.point.point_attempt)&&Number(value.point.point_attempt)>0&&object(value.source)&&exactKeys(value.source,'kind,relation,reason'));
 retainedDTO(value.retained);requireNative(exactKeys(value.retained,'native_result,native_binding,creator,integrity')&&exactKeys(value.retained.integrity,'readback,original_binding'));
 const creator=value.retained.creator,source=value.source;
 if(source.kind==='absent')requireNative(value.source_state===null&&source.relation===null&&typeof source.reason==='string'&&source.reason.length>0&&creator===null);
 else {
  requireNative((source.kind==='checkpoint'||source.kind==='final')&&['current_point_attempt','retained_source'].includes(String(source.relation))&&((source.kind==='final'&&source.relation==='current_point_attempt')?source.reason===null:typeof source.reason==='string'&&source.reason.length>0));
  stateRef(value.source_state);requireNative(value.source_state.identity.execution_id===selection.execution_id&&value.source_state.identity.point_id===pointId);
  requireNative(object(creator)&&exactKeys(creator,'calcjob_uuid,binding_uuid')&&uuid(creator.calcjob_uuid)&&(creator.binding_uuid===null||uuid(creator.binding_uuid))&&((creator.calcjob_uuid===selection.calcjob_uuid)===(creator.binding_uuid===null)));
 }
 return value as unknown as PointSource;
}
export function pointContext(source:PointSource):NativeContext|null {if(source.source.kind==='absent'||!source.source_state)return null;return {selection:source.provider_selection,point_id:source.point.point_id,native_binding_uuid:String(source.retained.native_binding.node_uuid),state_manifest_sha256:source.source_state.manifest.sha256,state_ref:source.source_state};}
export function pointChoice(token:string,run:Pick<Run,'uuid'|'children'|'plan_points'|'plan_fingerprint'>,job:string,pointId:string){
 const selection=selectionOptions(run).find(v=>v.calcjob_uuid===job),points=(run.plan_points??[]).filter(p=>p.execution_id===selection?.execution_id&&p.id===pointId),point=points.length===1?points[0]:undefined;
 return {selection,point,key:JSON.stringify([token,run.uuid,run.plan_fingerprint??null,job,pointId,selection??null,point?.id??null])};
}
export function nativePin(context:NativeContext){requireNative(uuid(context.selection.run_uuid)&&uuid(context.selection.calcjob_uuid)&&Number.isSafeInteger(context.selection.attempt)&&context.selection.attempt>0&&uuid(context.native_binding_uuid)&&hash(context.state_manifest_sha256));return {execution_id:context.selection.execution_id,attempt:context.selection.attempt,calcjob_uuid:context.selection.calcjob_uuid,provider:'native_nfs',point_id:context.point_id,native_binding_uuid:context.native_binding_uuid,state_manifest_sha256:context.state_manifest_sha256};}
export function readNativeEnvelope(value:unknown,context:NativeContext,operation:string):NativeEnvelope {
 boundedNative(value);nativePin(context);requireNative(object(value)&&value.schema==='qcl-negf.portal-selected-reader.v2'&&value.operation===operation&&value.provider==='native_nfs'&&value.selection_mode==='retained'&&sameValue(value.provider_selection,context.selection));
 stateRef(value.source_state);const state=value.source_state;
 requireNative(state.identity.execution_id===context.selection.execution_id&&state.identity.point_id===context.point_id&&state.manifest.sha256===context.state_manifest_sha256&&(!context.state_ref||sameValue(state,context.state_ref))&&object(value.retained)&&object(value.access)&&object(value.view));
 retainedDTO(value.retained);
 requireNative(value.retained.native_binding.node_uuid===context.native_binding_uuid&&value.access.status==='checked_ro_current_request'&&value.access.method==='local_binding_mountinfo_nofollow_domain'&&hash(value.access.config_sha256));
 requireNative(sameValue(value.view.source_state??value.view.source,state));
 return value as unknown as NativeEnvelope;
}
export function readManifest(value:unknown,context:NativeContext):NativeEnvelope<SavedManifest>{const envelope=readNativeEnvelope(value,context,'manifest');requireNative(object(envelope.view.views)&&Object.keys(envelope.view.views).length<=256);for(const [id,v] of Object.entries(envelope.view.views)){requireNative(object(v)&&v.view_id===id&&['available','unavailable'].includes(String(v.availability)));if(v.availability==='available')requireNative(Array.isArray(v.shape)&&v.shape.every(n=>Number.isSafeInteger(n)&&Number(n)>=0)&&typeof v.units==='string'&&object(v.allowed_selection)&&Number.isSafeInteger(v.allowed_selection.axis_count));}return envelope as NativeEnvelope<SavedManifest>;}
export function readSlice(value:unknown,context:NativeContext,viewId:string):NativeEnvelope<SavedSlice>{const envelope=readNativeEnvelope(value,context,'slice'),v=envelope.view;requireNative(v.view_id===viewId&&Array.isArray(v.returned_shape)&&v.returned_shape.length<=2&&v.returned_shape.every(n=>Number.isSafeInteger(n)&&Number(n)>=0)&&Array.isArray(v.axes)&&Array.isArray(v.logical_axes)&&v.logical_axes.length===v.returned_shape.length&&typeof v.units==='string'&&object(v.conventions)&&object(v.recorded_statuses));let cells=1;for(const n of v.returned_shape)cells*=Number(n);requireNative(cells<=65536);for(const a of v.axes)requireNative(object(a)&&typeof a.name==='string'&&typeof a.units==='string'&&Array.isArray(a.values)&&a.values.every(n=>typeof n==='number'&&Number.isFinite(n))&&(a.weights==null||(Array.isArray(a.weights)&&a.weights.length===a.values.length&&a.weights.every(n=>typeof n==='number'&&Number.isFinite(n)))));for(let i=0;i<v.logical_axes.length;i++){const axis=v.axes.find(a=>(a as Record<string,unknown>).name===(v.logical_axes as unknown[])[i]);requireNative(object(axis)&&Array.isArray(axis.values)&&axis.values.length===v.returned_shape[i]);}const verify=(numbers:unknown,masks:unknown,shape:number[])=>{if(shape.length){requireNative(Array.isArray(numbers)&&Array.isArray(masks)&&numbers.length===shape[0]&&masks.length===shape[0]);numbers.forEach((n,i)=>verify(n,masks[i],shape.slice(1)));}else requireNative((typeof numbers==='number'&&Number.isFinite(numbers)&&masks===null)||(numbers===null&&['nan','positive_infinity','negative_infinity'].includes(String(masks))));};verify(v.values,v.invalid_mask,v.returned_shape as number[]);return envelope as unknown as NativeEnvelope<SavedSlice>;}
export function selectionOptions(run:{uuid:string;children?:{uuid:string;execution_id:string;attempt:number}[]}):SavedSelection[]{const rows:SavedSelection[]=[];for(const child of run.children??[])if(child.execution_id&&Number.isSafeInteger(child.attempt)&&child.attempt>0&&!rows.some(r=>r.calcjob_uuid===child.uuid))rows.push({run_uuid:run.uuid,execution_id:child.execution_id,attempt:child.attempt,calcjob_uuid:child.uuid});return rows;}
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
