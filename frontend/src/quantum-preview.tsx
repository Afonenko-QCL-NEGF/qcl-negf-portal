import {useEffect,useRef,useState} from 'react';
import {api,errorMessage,quantumPreviewRaw} from './api.ts';
import {verifyPlan} from './configurator-data.ts';
import {plotScale,quantumChoices,quantumRequest,QuantumSession,readQuantum} from './quantum-preview-data.ts';
import type {QuantumArtifact,QuantumSource} from './quantum-preview-data.ts';

function Lines({z,rows,label,units,labels}:{z:number[];rows:number[][];label:string;units:string;labels?:string[]}){
 const x=plotScale(z,900),y=plotScale(rows.flat(),220);
 return <figure className="quantum-chart"><figcaption>{label} ({units}); z (nm), все {rows[0]?.length??0} curves</figcaption><svg viewBox="0 0 940 260" role="img" aria-label={label}>
  {rows[0]?.map((_,state)=><polyline key={state} fill="none" stroke={`hsl(${state*47%360} 65% 35%)`} strokeWidth="1.5" points={z.map((position,i)=>`${20+x(position)},${240-y(rows[i][state])}`).join(' ')}><title>{`${label}; ${labels?.[state]??`state ${state+1}`}; values ${rows.map(row=>row[state]).join(', ')} ${units}`}</title></polyline>)}
 </svg></figure>;
}
export function QuantumResult({value}:{value:QuantumArtifact}){
 const p=value.payload;
 return <div className="quantum-result"><p role="status">Bare basis diagnostic; κ=0, Hartree=0, no T±, Dirichlet. SCBA/Poisson не выполнялись; scientific not_assessed.</p>
  <p>Показаны все {p.shape[1]} states и {p.shape[0]} spatial nodes без изменения сетки. Энергии и band overlay — eV; ψ — nm^-1/2; density — nm^-1.</p>
  <table><caption>Все onsite κ0 eigenenergies (eV)</caption><thead><tr><th>State</th><th>E (eV)</th></tr></thead><tbody>{p.energy_eV.map((e,i)=><tr key={i}><th>{i+1}</th><td>{e}</td></tr>)}</tbody></table>
  <Lines z={p.z_nm} rows={p.z_nm.map((_,i)=>[p.band_edge_eV[i],...p.energy_eV])} labels={['bare material band',...p.energy_eV.map((_,i)=>`energy state ${i+1}`)]} label="Spatial bare material band overlay / onsite energies" units="eV"/>
  <p>Band overlay: zero-field material conduction band, no Hartree or bias tilt. Onsite energies не являются whole-superlattice bands.</p>
  <Lines z={p.z_nm} rows={p.psi_real} label="Re ψ" units="nm^-1/2"/>
  <Lines z={p.z_nm} rows={p.psi_imag} label="Im ψ" units="nm^-1/2"/>
  <Lines z={p.z_nm} rows={p.density_per_nm} label="Saved density" units="nm^-1"/>
  <details><summary>Exact source/passport, numerical inputs, scope and diagnostics</summary><pre>{JSON.stringify(value,null,2)}</pre></details>
  <p>Normalization и spatial phase заданы producer; UI их не пересчитывает. В degenerate subspace отдельные ψ/density не уникальны; сравним projector / summed block density. JIT/peak/elapsed: только recorded measurements, missing остаётся unknown.</p>
 </div>;
}
export function QuantumPreview({token,plan,sha256,parentContext=''}:{token:string;plan:string;sha256:string;parentContext?:string}){
 const session=useRef(new QuantumSession()),context=token+'\0'+plan+'\0'+sha256+'\0'+parentContext;
 const latest=useRef(context);if(latest.current!==context){latest.current=context;session.current.invalidate();}
 const [source,setSource]=useState<QuantumSource|null>(null),[availability,setAvailability]=useState('Проверка installed runtime binding…');
 const [sourceContext,setSourceContext]=useState('');const activeSource=sourceContext===context?source:null;
 const [selected,setSelected]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState(''),[result,setResult]=useState<{raw:string;value:QuantumArtifact;context:string}|null>(null);
 const visible=result?.context===context?result:null;
 let choices:ReturnType<typeof quantumChoices>=[];let selectionError='';try{choices=quantumChoices(plan);}catch(error){selectionError=errorMessage(error);}
 useEffect(()=>{let active=true;setSource(null);setSelected('');setResult(null);setBusy(false);
  void api<unknown>(token,'/config').then(value=>{if(!active)return;
   const cfg=value&&typeof value==='object'&&'quantum_preview' in value?value.quantum_preview:null;
   if(cfg&&typeof cfg==='object'&&'available' in cfg&&cfg.available===true&&'runner_source_id' in cfg&&typeof cfg.runner_source_id==='string'&&'core_source_id' in cfg&&typeof cfg.core_source_id==='string'){
    setSource({runner_source_id:cfg.runner_source_id,core_source_id:cfg.core_source_id});setSourceContext(context);setAvailability('Source binding доступен; fresh controller memory admission выполняется при запросе.');
   }else setAvailability('Quantum preview unavailable: installed runtime/source binding не подтверждён.');
  }).catch(e=>{if(active)setAvailability(errorMessage(e));});
  return()=>{active=false;session.current.invalidate();};
 },[context,token]);
 async function run(){
  if(!activeSource)return;const operation=session.current.begin();if(!operation)return;
  const snapshot=latest.current;setBusy(true);setError('');setResult(null);
  try{const choice=choices.find(row=>JSON.stringify(row)===selected)??null;
   const request=quantumRequest(plan,sha256,choice,activeSource);await verifyPlan(plan,sha256);
   if(!session.current.current(operation)||snapshot!==latest.current)return;
   const body=JSON.stringify(request),requestSHA=await verifyPlan(body);
   const raw=await quantumPreviewRaw(token,body,operation.signal),value=readQuantum(raw,request);
   if(value.identity.request_sha256!==requestSHA)throw new Error('Request SHA mismatch');
   if(session.current.current(operation)&&snapshot===latest.current)setResult({raw,value,context:snapshot});
  }catch(e){if(session.current.current(operation)&&snapshot===latest.current)setError(errorMessage(e));}
  finally{session.current.finish(operation);if(snapshot===latest.current)setBusy(false);}
 }
 function cancel(){session.current.invalidate();setBusy(false);setResult(null);setError('Preview отменён; server подтверждает cleanup перед повторным использованием общего slot.');}
 function download(){if(!visible)return;const url=URL.createObjectURL(new Blob([visible.raw],{type:'application/json'}));const link=document.createElement('a');link.href=url;link.download='quantum-preview.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),0);}
 return <section className="quantum-preview" aria-label="Basis-only quantum preview"><h4>Quantum basis preview — без transport solve</h4><p>{availability}</p>
  {selectionError&&<p role="alert">{selectionError}</p>}
  <label>Явный execution / point / variant<select value={selected} disabled={busy} onChange={e=>{session.current.invalidate();setSelected(e.target.value);setResult(null);setError('');}}><option value="">Выберите точку</option>{choices.map(row=><option key={row.point_id} value={JSON.stringify(row)}>{row.execution_id} / {row.point_id} / {row.variant_id}</option>)}</select></label>
  <button type="button" disabled={busy||!activeSource||!selected||!!selectionError} onClick={()=>void run()}>Построить bare quantum preview (до 180 s)</button>
  {busy&&<><p role="status">Basis/eigen/serialization в общем Configurator slot; no retry/coarsening.</p><button type="button" onClick={cancel}>Отменить preview</button></>}
  {error&&<p role="alert" className="error">{error}</p>}
  {visible&&<><QuantumResult value={visible.value}/><button type="button" onClick={download}>Скачать exact producer JSON</button></>}
 </section>;
}
