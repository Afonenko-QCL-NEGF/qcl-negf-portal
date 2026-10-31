import {useEffect,useRef,useState} from 'react';
import {api,errorMessage} from './api';
import type {Run} from './api';
import {SavedSession,selectionOptions} from './saved-results-data';
import {checkedLive,object} from './live-performance-data';
import type {RecordValue} from './live-performance-data';

const show=(v:unknown):string=>v==null?'unknown':typeof v==='object'?JSON.stringify(v):String(v);
function NativeHistoryRows({data}:{data:RecordValue}){
 const coordinates=data.coordinates,columns=data.columns;
 if(!object(coordinates)||!Array.isArray(coordinates.sequence)||coordinates.sequence.length>512||!Array.isArray(columns)||columns.length>16||!columns.every(object))return <p>Native table unavailable: inspect exact response.</p>;
 const rows=coordinates.sequence,keys=['sequence','iteration','outer_iteration','source_attempt'];
 return <div className="diagnostic-table"><table><thead><tr>{keys.map(k=><th key={k}>{k}</th>)}<th>break_before</th>{columns.map((c,i)=><th key={i}>{show(c.name)} ({show(c.units)})</th>)}</tr></thead><tbody>{rows.map((_,i)=><tr key={i}>{keys.map(k=><td key={k}>{show(Array.isArray(coordinates[k])?coordinates[k][i]:null)}</td>)}<td>{show(Array.isArray(data.break_before)?data.break_before[i]:null)}</td>{columns.map((c,j)=><td key={j}>{show(Array.isArray(c.values)?c.values[i]:null)}{Array.isArray(c.invalid_mask)&&c.invalid_mask[i]!=null?' · native '+show(c.invalid_mask[i]):''}</td>)}</tr>)}</tbody></table></div>;
}
export function LivePerformance({token,run}:{token:string;run:Run}){
 const options=selectionOptions(run),[choice,setChoice]=useState('');
 const exact=options.find(v=>`${v.execution_id}/${v.attempt}/${v.calcjob_uuid}`===choice);
 const [mode,setMode]=useState<'retained'|'current'>('retained'),[anchor,setAnchor]=useState('');
 const [point,setPoint]=useState(''),[attempt,setAttempt]=useState('');
 const [phase,setPhase]=useState('scba'),[columns,setColumns]=useState(''),[start,setStart]=useState('0'),[stop,setStop]=useState(''),[step,setStep]=useState('1');
 const [descriptor,setDescriptor]=useState<RecordValue|null>(null),[slice,setSlice]=useState<RecordValue|null>(null),[performance,setPerformance]=useState<RecordValue|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const session=useRef(new SavedSession()),context=JSON.stringify([token,run.uuid,exact,mode,anchor,point,attempt,phase,columns,start,stop,step]);
 const current=useRef(context);if(current.current!==context){session.current.invalidate();current.current=context;}
 useEffect(()=>{session.current.invalidate();setDescriptor(null);setSlice(null);setPerformance(null);setError('');setBusy(false);return()=>session.current.invalidate();},[token,run.uuid,choice,mode,anchor,point,attempt]);
 useEffect(()=>{session.current.invalidate();setSlice(null);setBusy(false);},[phase,columns,start,stop,step]);
 async function load(kind:'live-source'|'live-history'|'performance'){
  if(!exact)return;
  const identity={run_uuid:run.uuid,execution_id:exact.execution_id,attempt:exact.attempt,calcjob_uuid:exact.calcjob_uuid};
  const payload:RecordValue={execution_id:exact.execution_id,attempt:exact.attempt,calcjob_uuid:exact.calcjob_uuid};
  if(kind==='performance'){
   if(!/^[a-f0-9]{64}$/.test(anchor)){setError('Укажите явный SHA-256 anchor');return;}
   Object.assign(payload,{mode,anchor_key:anchor});setPerformance(null);
  }
  if(kind!=='performance'){
   const n=Number(attempt);if(!Number.isSafeInteger(n)||n<1||!point){setError('Укажите exact point и положительный native source attempt');return;}
   Object.assign(payload,{point_id:point,source_attempt:n});
  }
  if(kind==='live-history'){
   if(!descriptor)return;
   const names=columns.split(',').map(v=>v.trim()),a=Number(start),b=Number(stop),c=Number(step);
   if(![a,b,c].every(Number.isSafeInteger)||a<0||b<=a||c<1||Math.ceil((b-a)/c)>512||!names.length||names.length>16||names.some(v=>!v)||new Set(names).size!==names.length){setError('Нужны 1–16 native columns и явный диапазон ≤512 строк');return;}
   Object.assign(payload,{phase,index_sha256:descriptor.index_sha256,generation:descriptor.generation,selection:{start:a,stop:b,step:c,columns:names}});
  }
  const g=session.current.begin();setBusy(true);setError('');
  await session.current.receive(g,async()=>{
   const value=checkedLive(await api(token,`/runs/${encodeURIComponent(run.uuid)}/live-results/${kind}`,payload),identity,kind,kind==='performance'?{mode,anchor_key:anchor}:undefined);
   if(kind!=='performance'){
    const owner=kind==='live-history'?value.source_selection:value.selection;
    if(!object(owner)||owner.point_id!==point||owner.source_attempt!==Number(attempt))throw new Error('Exact native point identity mismatch');
    if(kind==='live-history'&&(value.index_sha256!==descriptor?.index_sha256||value.generation!==descriptor?.generation))throw new Error('source_changed');
   }
   return value;
  },value=>{if(!object(value))throw new Error('Validated response object required');if(kind==='performance')setPerformance(value);else if(kind==='live-source'){setDescriptor(value);setSlice(null);}else setSlice(value);setBusy(false);},err=>{setError(errorMessage(err));if(kind==='performance')setPerformance(null);if(kind!=='performance'){setSlice(null);if(kind==='live-source')setDescriptor(null);}setBusy(false);});
 }
 const view=performance&&object(performance.view)?performance.view:null;
 const summary=view&&object(view.summary)?view.summary:null;
 const source=performance&&object(performance.source_metadata)?performance.source_metadata:null;
 return <section aria-label="Live history и стоимость"><h3>Live history и измеренная стоимость</h3>
 <p>Чтение по запросу. Closed prefix отражает опубликованные листья; публикация не heartbeat и не научная приёмка. Стоимость относится к выбранному execution attempt; borrowed point не получает стоимость этого процесса.</p>
 <label>Exact execution / attempt / CalcJob<select value={choice} onChange={e=>setChoice(e.target.value)}><option value="">Выберите</option>{options.map(v=>{const id=`${v.execution_id}/${v.attempt}/${v.calcjob_uuid}`;return <option key={id} value={id}>{id}</option>;})}</select></label>
 <label>Performance mode<select value={mode} onChange={e=>setMode(e.target.value as 'retained'|'current')}><option value="retained">retained</option><option value="current">current</option></select></label>
 <label>Explicit performance anchor SHA-256<input value={anchor} onChange={e=>setAnchor(e.target.value)} spellCheck={false}/></label>
 <button disabled={!exact||busy||!/^[a-f0-9]{64}$/.test(anchor)} onClick={()=>void load('performance')}>Прочитать exact performance summary</button>
 {performance&&<><p>Performance: {show(performance.status)} · {show(source?.reason)}; mode {show(performance.mode)}; anchor {show(performance.anchor_key)}.</p>
 {summary&&<><table><thead><tr><th>Измерение / native единицы</th><th>Значение / coverage</th></tr></thead><tbody>{[['cpu_coverage','CPU seconds / covered logical core seconds; nullable capacity'],['max_observed_rss_bytes','Observed RSS bytes; last sampled peak, terminal tail may be unknown'],['measurement_coverage','Resource gaps, sample resolution and source coverage'],['producer_phase_measurements','Producer inclusive phase measurements'],['producer_phase_seconds','Producer phase seconds (not job wall)'],['producer_phase_calls','Producer phase calls'],['phase_attribution','Phase attribution'],['warmup_seconds','Warmup seconds; JIT remains unknown unless measured']].map(([key,label])=><tr key={key}><td>{label}</td><td>{show(summary[key])}</td></tr>)}</tbody></table><p>Root Julia process с потоками; descendants excluded. Total scheduler grant и JIT: unknown. Пропуски и missing capacity сохраняются, unknown не равен нулю.</p></>}
 {view&&<><p>Coverage / retention: {show(view.coverage)} · {show(view.retention)}</p><p>Reader I/O: {show(view.io_counters)}</p><p>Collector observer self-cost: {show(view.observer_self_cost)}</p><p>Actor I/O / heap / peak RSS: unknown. Эти значения не являются полной стоимостью HTTP.</p></>}
 <details><summary>Exact performance binding / coverage / unknown reasons</summary><pre>{JSON.stringify(performance,null,2)}</pre></details></>}
 <label>Frozen point ID<input value={point} onChange={e=>setPoint(e.target.value)}/></label><label>Native source attempt<input inputMode="numeric" value={attempt} onChange={e=>setAttempt(e.target.value)}/></label>
 <button disabled={!exact||!point||!attempt||busy} onClick={()=>void load('live-source')}>Прочитать / обновить live descriptor</button>
 {descriptor&&<><p>Closed prefix generation {show(descriptor.generation)}; cutoff {show(descriptor.cutoff)}; producer updated {show(descriptor.updated_unix)}; observed {show(descriptor.observed_unix)}. Clock skew possible. Incomplete: {show(descriptor.live_publication_incomplete)}.</p>
 <label>Native phase<select value={phase} onChange={e=>setPhase(e.target.value)}><option value="scba">scba</option><option value="outer">outer</option></select></label><label>Native value columns через запятую (descriptor не открывает payload)<input value={columns} onChange={e=>setColumns(e.target.value)}/></label>
 <label>Start row ordinal<input value={start} onChange={e=>setStart(e.target.value)}/></label><label>Stop exclusive<input value={stop} onChange={e=>setStop(e.target.value)}/></label><label>Step<input value={step} onChange={e=>setStep(e.target.value)}/></label>
 <button disabled={busy||!columns||!stop} onClick={()=>void load('live-history')}>Прочитать ≤512 native rows</button><details><summary>Closed prefix identity / coverage</summary><pre>{JSON.stringify(descriptor,null,2)}</pre></details></>}
 {slice&&<><p>Native coordinates, units, ExactScalar, invalid masks и break_before сохранены; пропуски не соединяются кривой.</p><NativeHistoryRows data={slice}/><details><summary>Exact native history rows / coverage / I/O</summary><pre>{JSON.stringify(slice,null,2)}</pre></details></>}
 {busy&&<><p role="status">Ограниченное чтение выбранных bytes…</p><button onClick={()=>{session.current.invalidate();setPerformance(null);setDescriptor(null);setSlice(null);setBusy(false);}}>Отменить ожидание чтения</button></>}{error&&<p role="alert">{error}</p>}
 </section>;
}
