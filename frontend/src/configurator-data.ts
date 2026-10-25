import {ApiError} from './api.ts';
import {readStructureEditor} from './structure-editor-data.ts';
import type {StructureCapability} from './structure-editor-data.ts';

export interface CatalogTemplate {
  id: string; name: string; study: string; study_sha256: string; description: string;
  model: {name: string; path: string; sha256: string};
  protocol: {name: string; path: string; sha256: string; version: string};
}
export interface Catalog {status: 'available'; catalog_source_id: string; templates: CatalogTemplate[]}
export interface Preview {
  structure_editor?: StructureCapability;
  status: 'available'; draft_sha256: string; semantic_draft_sha256: string;
  catalog_source_id: string; template: CatalogTemplate; draft_yaml: string;
  short_form: {lossless: boolean; reason: string | null};
  variant_diffs: unknown[]; resolved_inputs: unknown[]; effective_defaults: unknown[];
  default_rule_sources: unknown; plan: string; plan_sha256: string;
  fingerprint: string; scientific_fingerprint: string;
  counts: {points: number; executions: number}; estimates: unknown;
}
export interface PlanSelection {plan: string; sha256?: string}
export interface FormFields {
  temperature_K?: number; voltage_per_period_mV?: number;
  voltage_mode?: 'independent' | 'strict'; scba_to_poisson?: string; variant?: unknown;
}
const sha=/^[a-f0-9]{64}(?![\s\S])/;
const object=(v:unknown): v is Record<string,unknown> => !!v && typeof v==='object' && !Array.isArray(v);
const text=(v:unknown): v is string => typeof v==='string' && v.length>0;
function finiteTree(value:unknown,depth=0):boolean {
  if(depth>64)return false;
  if(value===null || typeof value==='string' || typeof value==='boolean')return true;
  if(typeof value==='number')return Number.isFinite(value)&&(!Number.isInteger(value)||Number.isSafeInteger(value));
  if(Array.isArray(value))return value.every(v=>finiteTree(v,depth+1));
  return object(value)&&Object.values(value).every(v=>finiteTree(v,depth+1));
}
function template(value:unknown): value is CatalogTemplate {
  if(!object(value)||!['id','name','study','description'].every(k=>text(value[k])) || !sha.test(String(value.study_sha256)))return false;
  for(const k of ['model','protocol']) {
    const part=value[k];if(!object(part)||!text(part.name)||!text(part.path)||!sha.test(String(part.sha256)))return false;
    if(k==='protocol'&&!text(part.version))return false;
  }
  return true;
}
function refusal(value:unknown):never {
  if(object(value)&&object(value.error))throw new Error(['code','path','message'].map(k=>typeof value.error==='object' && value.error && k in value.error ? String((value.error as Record<string,unknown>)[k]).slice(0,512):'').filter(Boolean).join(' · '));
  throw new Error('Конфигуратор недоступен: неподдерживаемый ответ');
}
export function readCatalog(value:unknown):Catalog {
  if(!object(value)||value.status!=='available')return refusal(value);
  if(!text(value.catalog_source_id)||!Array.isArray(value.templates)||value.templates.length<1||value.templates.length>32 || !value.templates.every(template) || new Set(value.templates.map(t=>t.id)).size!==value.templates.length)throw new Error('Некорректный каталог конфигуратора');
  return value as unknown as Catalog;
}
export function readPreview(value:unknown,source:string,id:string):Preview {
  if(!object(value)||value.status!=='available')return refusal(value);
  if(!finiteTree(value)||value.catalog_source_id!==source||!template(value.template)||value.template.id!==id ||
      !['draft_sha256','semantic_draft_sha256','plan_sha256','fingerprint','scientific_fingerprint'].every(k=>sha.test(String(value[k]))) ||
      typeof value.plan!=='string'||!value.plan||typeof value.draft_yaml!=='string'||!object(value.short_form)||typeof value.short_form.lossless!=='boolean'|| !(value.short_form.reason===null||typeof value.short_form.reason==='string')||
      !['variant_diffs','resolved_inputs','effective_defaults'].every(k=>Array.isArray(value[k]))||value.default_rule_sources===undefined||value.estimates===undefined||
      !object(value.counts)||!['points','executions'].every(k=>Number.isSafeInteger((value.counts as Record<string,unknown>)[k])&&Number((value.counts as Record<string,unknown>)[k])>=0))throw new Error('Некорректный или устаревший preview');
  if(value.structure_editor!==undefined)readStructureEditor(value.structure_editor,{catalog_source_id:source,template_id:id,template_sha256:value.template.study_sha256,model:value.template.model});
  return value as unknown as Preview;
}
export function makeRequest(mode:'form'|'yaml',id:string,source:string,fields:FormFields,yaml:string) {
  if(mode==='form'&&!finiteTree(fields))throw new Error('Нужны конечные safe численные значения');
  return mode==='yaml'?{mode,template_id:id,catalog_source_id:source,yaml}:{mode,template_id:id,catalog_source_id:source,fields};
}
/** Only explicit count controls; all other expert leaves stay in original full YAML. */
export function makeNumericalVariant(id:string,comparison:string,paths:string,counts:Record<string,string>) {
  const numerical:Record<string,number>={};
  for(const [key,raw] of Object.entries(counts)) {
    if(raw==='')continue;
    if(!['energy_nodes','momentum_nodes','spatial_nodes','basis_states'].includes(key))throw new Error('Unsupported count control');
    // No trimming or coercion: blank omits; otherwise canonical positive decimal digits only.
    if(!/^[1-9][0-9]*(?![\s\S])/.test(raw))throw new Error('Numerical count: только canonical decimal integer без пробелов, знака, дроби или exponent; expert input задаётся полным YAML');
    const value=Number(raw);
    if(!Number.isSafeInteger(value)||value<1)throw new Error('Для numerical count нужно положительное safe integer; expert input задаётся полным YAML');
    numerical[key]=value;
  }
  const controlled=paths.split('\n').map(path=>path.trim()).filter(Boolean);
  if(!id||!comparison||!controlled.length||!Object.keys(numerical).length)throw new Error('Для варианта нужны ID, comparison_kind, controlled_paths и explicit numerical override');
  return {id,comparison_kind:comparison,controlled_paths:controlled,overrides:{numerical}};
}
export async function verifyPlan(plan:string,expected?:string):Promise<string> {
  const digest=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(plan));
  const actual=Array.from(new Uint8Array(digest),v=>v.toString(16).padStart(2,'0')).join('');
  if(expected!==undefined&&actual!==expected)throw new Error('SHA-256 frozen plan не совпадает');
  return actual;
}
/** Production read guard covers late successes, failures and digest verification. */
export class PreviewSession {
  private generation=0;
  invalidate(){this.generation++;}
  begin(){return ++this.generation;}
  current(value:number){return value===this.generation;}
  async receive<T>(generation:number,request:()=>Promise<T>,apply:(v:T)=>void,error:(e:unknown)=>void) {
    try {const value=await request();if(this.current(generation))apply(value);}
    catch(err){if(this.current(generation))error(err);}
  }
}
/** No state-render delay can permit two POSTs. Uncertainty survives edits. */
export class SubmissionGate {
  private selection:PlanSelection|null=null;
  private busy=false;
  private uncertain=false;
  select(value:PlanSelection|null){this.selection=value;}
  begin(){if(this.busy||this.uncertain||!this.selection)return null;this.busy=true;return this.selection;}
  current(value:PlanSelection){return this.selection===value&&!this.uncertain;}
  finish(uncertain:boolean){this.uncertain ||= uncertain;this.busy=false;}
}
export function mutationUncertain(error:unknown):boolean {
  return !(error instanceof ApiError) || error.outcome==='unknown'||!!error.unresolvedOperationId ||
    (!error.outcome&&![400,401,403,404,409,413,422,429].includes(error.status));
}
