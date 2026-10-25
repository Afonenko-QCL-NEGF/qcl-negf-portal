export interface QuantumChoice {execution_id:string;variant_id:string;point_id:string}
export interface QuantumSource {runner_source_id:string;core_source_id:string}
export interface QuantumRequest extends QuantumChoice {schema:'qcl-negf.quantum-preview-request.v1';plan:string;plan_sha256:string;expected_runner_source_id:string;expected_core_source_id:string;budget_policy_id:'quantum-preview-reference-v1'}
export interface QuantumArtifact {schema:'qcl-negf.quantum-preview.v1';status:'available';preview_id:string;payload_sha256:string;identity:Record<string,unknown>;payload:{shape:[number,number];dimensions:Record<string,number>;z_nm:number[];energy_eV:number[];band_edge_eV:number[];psi_real:number[][];psi_imag:number[][];density_per_nm:number[][];units:Record<string,string>;semantics:Record<string,unknown>;diagnostics:Record<string,unknown>};assessment:Record<string,string>;measurements:unknown}
const object=(v:unknown):v is Record<string,unknown>=>!!v&&typeof v==='object'&&!Array.isArray(v);
const text=(v:unknown):v is string=>typeof v==='string'&&v.length>0&&v.length<=256;
const sha=(v:unknown):v is string=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
const rev=(v:unknown):v is string=>typeof v==='string'&&/^[a-f0-9]{40}$/.test(v);
export function quantumChoices(plan:string):QuantumChoice[]{
 if(new TextEncoder().encode(plan).length>1048576)throw new Error('Frozen plan превышает 1 MiB');
 const value:unknown=JSON.parse(plan);
 if(!object(value)||!Array.isArray(value.executions)||!Array.isArray(value.points)||value.executions.length>64||value.points.length>256)throw new Error('Unsupported frozen plan');
 const executions=new Map<string,string>();for(const row of value.executions){if(!object(row)||!text(row.id)||!text(row.variant_id)||executions.has(row.id))throw new Error('Неоднозначный execution');executions.set(row.id,row.variant_id);}
 const seen=new Set<string>();return value.points.map(row=>{if(!object(row)||!text(row.id)||!text(row.execution_id)||seen.has(row.id)||!executions.has(row.execution_id))throw new Error('Неоднозначный point');seen.add(row.id);return {execution_id:row.execution_id,point_id:row.id,variant_id:executions.get(row.execution_id)!};});
}
export function quantumRequest(plan:string,hash:string,choice:QuantumChoice|null,source:QuantumSource):QuantumRequest{
 if(!choice||!sha(hash)||!rev(source.runner_source_id)||!rev(source.core_source_id)||!quantumChoices(plan).some(p=>p.point_id===choice.point_id&&p.execution_id===choice.execution_id&&p.variant_id===choice.variant_id))throw new Error('Явно выберите exact execution/point/variant');
 return {schema:'qcl-negf.quantum-preview-request.v1',plan,plan_sha256:hash,...choice,expected_runner_source_id:source.runner_source_id,expected_core_source_id:source.core_source_id,budget_policy_id:'quantum-preview-reference-v1'};
}
function finiteTree(v:unknown,depth=0):boolean{
 if(depth>64)return false;if(v===null||typeof v==='string'||typeof v==='boolean')return true;
 if(typeof v==='number')return Number.isFinite(v);
 if(Array.isArray(v))return v.every(x=>finiteTree(x,depth+1));return object(v)&&Object.values(v).every(x=>finiteTree(x,depth+1));
}
export function readQuantum(raw:string,request:QuantumRequest):QuantumArtifact{
 if(new TextEncoder().encode(raw).length>2097152)throw new Error('Quantum JSON превышает 2 MiB');
 const value:unknown=JSON.parse(raw);
 if(!object(value)||!finiteTree(value)||value.schema!=='qcl-negf.quantum-preview.v1'||value.status!=='available'||!sha(value.preview_id)||!sha(value.payload_sha256)||value.payload_digest_encoding!=='qcl-negf-canonical-bytes-v1'||value.budget_policy_id!=='quantum-preview-reference-v1'||value.policy_revision!==1||!object(value.identity)||!object(value.payload))throw new Error('Некорректный quantum DTO');
 const id=value.identity,p=value.payload;
 if(!['request_sha256','fingerprint','scientific_fingerprint'].every(key=>sha(id[key]))||!object(id.point_passport))throw new Error('Missing frozen passport');
 const frozen:unknown=JSON.parse(request.plan);if(!object(frozen)||id.fingerprint!==frozen.fingerprint||id.scientific_fingerprint!==frozen.scientific_fingerprint||!Array.isArray(frozen.points))throw new Error('Frozen fingerprints mismatch');
 const point=frozen.points.find(row=>object(row)&&row.id===request.point_id&&row.execution_id===request.execution_id);
 if(!object(point)||!object(point.effective_inputs))throw new Error('Frozen point passport missing');
 for(const key of ['temperature_K','voltage_per_period_V','configuration_hash','hash_encoding','axes_applied'])if(!Object.is(id.point_passport[key],point.effective_inputs[key]))throw new Error('Point passport mismatch');
 for(const key of ['plan_sha256','execution_id','point_id','variant_id'] as const)if(id[key]!==request[key])throw new Error('Устаревшая quantum identity');
 if(id.runner_source_id!==request.expected_runner_source_id||id.core_source_id!==request.expected_core_source_id||!object(id.runtime)||id.runtime.runner_source_id!==request.expected_runner_source_id||id.runtime.core_source_id!==request.expected_core_source_id||!sha(id.runtime.context_sha256))throw new Error('Source binding mismatch');
 if(!Array.isArray(p.shape)||p.shape.length!==2||!p.shape.every(n=>Number.isSafeInteger(n)&&n>=1)||!object(p.dimensions))throw new Error('Invalid shape');
 const nz=p.shape[0] as number,nb=p.shape[1] as number;if(nb>32||nz*nb>16384||p.dimensions.Nz!==nz||p.dimensions.Nb!==nb)throw new Error('Shape exceeds policy');
 const vector=(v:unknown,n:number):v is number[]=>Array.isArray(v)&&v.length===n&&v.every(x=>typeof x==='number'&&Number.isFinite(x));
 if(!vector(p.z_nm,nz)||!vector(p.energy_eV,nb)||!vector(p.band_edge_eV,nz)||p.z_nm.some((x,i,a)=>i>0&&x<=a[i-1]))throw new Error('Invalid axes');
 for(const key of ['psi_real','psi_imag','density_per_nm']){const matrix=p[key];if(!Array.isArray(matrix)||matrix.length!==nz||!matrix.every(row=>vector(row,nb)))throw new Error('Invalid matrix');}
 if(!object(p.units)||p.units.energy!=='eV'||p.units.z!=='nm'||p.units.psi!=='nm^-1/2'||p.units.density!=='nm^-1'||!object(p.semantics)||!object(p.diagnostics))throw new Error('Units unavailable');
 for(const [key,wanted] of Object.entries({scope:'bare_onsite_k0',hartree:'zero',kappa:0,interperiod_hopping:'excluded',window_boundary:'Dirichlet'}))if(p.semantics[key]!==wanted)throw new Error('Unsupported physical scope');
 if(!object(value.assessment)||value.assessment.SCBA!=='not_performed'||value.assessment.Poisson!=='not_performed'||value.assessment.scientific!=='not_assessed')throw new Error('Invalid assessment');
 return value as unknown as QuantumArtifact;
}
export class QuantumSession{
 private active:{generation:number;controller:AbortController}|null=null;private generation=0;
 invalidate(){this.generation++;this.active?.controller.abort();this.active=null;}
 begin(){if(this.active)return null;const value={generation:++this.generation,controller:new AbortController()};this.active=value;return {generation:value.generation,signal:value.controller.signal};}
 current(value:{generation:number}){return value.generation===this.generation;}
 finish(value:{generation:number}){if(this.active?.generation===value.generation)this.active=null;}
}
/** Rendering-only mapping stays finite for signed extrema and singleton domains. */
export function plotScale(values:number[],extent:number){const magnitude=Math.max(1,...values.map(Math.abs));const low=Math.min(...values.map(v=>v/magnitude)),high=Math.max(...values.map(v=>v/magnitude));return (v:number)=>high===low?extent/2:(v/magnitude-low)/(high-low)*extent;}
