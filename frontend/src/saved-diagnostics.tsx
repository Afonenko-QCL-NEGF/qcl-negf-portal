import {useEffect,useRef,useState} from 'react';
import {api,errorMessage} from './api.ts';
import {SavedSession,displayScale} from './saved-results-data.ts';
import {exactText,historyPaths,historySelection,readDiagnostics,readHistory} from './saved-diagnostics-data.ts';
import type {DiagnosticContext,Diagnostics,HistorySlice} from './saved-diagnostics-data.ts';
export function HistoryPlot({data,column,axis}:{data:HistorySlice;column:string;axis:'iteration'|'sequence'}){
 const paths=historyPaths(data,column,axis),values=paths.flat();if(!values.length)return <p>Нет представимой конечной кривой: exact coordinates/values и masks сохранены в таблице.</p>;
 let x:(v:number)=>number,y:(v:number)=>number;
 try{const sx=displayScale(values.map(v=>v[0])),sy=displayScale(values.map(v=>v[1]));x=v=>50+sx.position(v)*580;y=v=>240-sy.position(v)*200;for(const p of values)if(!Number.isFinite(x(p[0]))||!Number.isFinite(y(p[1])))throw new Error('geometry');}catch{return <p>Геометрия графика недоступна; исходная таблица сохранена.</p>;}
 const units=data.columns.find(c=>c.name===column)?.units;
 return <svg className="diagnostic-plot" viewBox="0 0 680 290" role="img" aria-label={`${column} versus native ${axis}`}><path d="M50 25V240H630" stroke="#546b82" fill="none"/><text x="280" y="280">Native {axis}</text><text x="50" y="18">{`${column} (${units??'unknown units'})`}</text>{paths.map((path,i)=><g key={i} stroke="#215f96" fill="#215f96">{path.length>1&&<polyline fill="none" points={path.map(p=>`${x(p[0])},${y(p[1])}`).join(' ')}/>} {path.map((p,j)=><circle key={j} cx={x(p[0])} cy={y(p[1])} r="3"><title>{`${axis}=${p[0]}, ${column}=${p[1]} ${units??'unknown units'}`}</title></circle>)}</g>)}</svg>;
}
export function DiagnosticSummary({data}:{data:Diagnostics}){
 return <div><p>Closed: {exactText(data.closed)}; generation {exactText(data.generation)}. Это сохранённый committed prefix, не live. Process/quality: {exactText(data.recorded_statuses.terminal_status)} / {exactText(data.recorded_statuses.quality)}.</p>
 <p>Сходимость: {exactText(data.recorded_statuses.converged)}; recorded scientific_accepted: {exactText(data.recorded_statuses.scientific_accepted)}. Физические проверки, достаточность сетки и эксперимент: unknown, если отсутствуют исходные свидетельства. Кривая сама не устанавливает приёмку.</p>
 <p>History: {data.history.status}; {data.history.reason??'metadata available'}. Owner SHA пока не проверен полностью.</p>
 <h5>Сохранённая стоимость checkpoint payload</h5><p>Это не job wall/CPU, не allocated grant и не суммарная стоимость запуска.</p>
 {data.resources.checkpoint?<table><thead><tr><th>Поле / единицы</th><th>Исходное значение</th></tr></thead><tbody>{[['checkpoint_payload_seconds','seconds'],['checkpoint_payload_bytes','bytes'],['includes_commit_publication','boolean'],['jit_separately_measured','boolean'],['scope','scope']].map(([key,unit])=><tr key={key}><td>{key} ({unit})</td><td>{exactText(data.resources.checkpoint?.[key])}</td></tr>)}</tbody></table>:<p>{data.resources.reason??'checkpoint_metadata_missing'}</p>}
 <p>Performance summary: {exactText(data.resources.performance_summary.status)} · {exactText(data.resources.performance_summary.reason)}. Missing RSS/CPU/wall остаются unknown.</p>
 <details><summary>Native identity, raw statuses, coverage и I/O</summary><pre>{JSON.stringify({identity:data.identity,statuses:data.recorded_statuses,coverage:data.coverage,integrity:data.integrity,io_counters:data.io_counters,limits:data.limits},null,2)}</pre></details></div>;
}
export function SavedDiagnostics({token,context}:{token:string;context:DiagnosticContext}){
 const session=useRef(new SavedSession()),current=useRef('');const key=JSON.stringify([token,context]);
 if(current.current!==key){session.current.invalidate();current.current=key;}
 const [descriptor,setDescriptor]=useState<{key:string;value:Diagnostics}|null>(null),[history,setHistory]=useState<{key:string;value:HistorySlice}|null>(null);
 const [phase,setPhase]=useState<'scba'|'outer'|''>(''),[columns,setColumns]=useState<string[]>([]),[start,setStart]=useState(''),[stop,setStop]=useState(''),[step,setStep]=useState('');
 const [axis,setAxis]=useState<'iteration'|'sequence'|''>(''),[plotColumn,setPlotColumn]=useState(''),[busy,setBusy]=useState(''),[error,setError]=useState<{key:string;message:string}|null>(null);
 const data=descriptor?.key===key?descriptor.value:null,slice=history?.key===key?history.value:null;
 useEffect(()=>{session.current.invalidate();setDescriptor(null);setHistory(null);setPhase('');setColumns([]);setStart('');setStop('');setStep('');setAxis('');setPlotColumn('');setBusy('');setError(null);return()=>session.current.invalidate();},[key]);
 function invalidate(){session.current.invalidate();setHistory(null);setError(null);setBusy('');}
 async function load(operation:'diagnostics'|'history'){
  const g=session.current.begin();setBusy(key);setError(null);
  await session.current.receive(g,async()=>{
   const pin={execution_id:context.selection.execution_id,attempt:context.selection.attempt,calcjob_uuid:context.selection.calcjob_uuid,provider:context.source.provider,point_id:context.point_id,source_commit_sha256:context.source_commit_sha256,metadata_sha256:context.metadata_sha256,...(context.source.provider==='nfs_registered'?{source_uuid:context.source.source_uuid!,source_sha256:context.source.source_sha256!}:{})};
   const base=`/runs/${encodeURIComponent(context.selection.run_uuid)}/saved-results/`;
   if(operation==='diagnostics'){const query=new URLSearchParams(Object.entries(pin).map(([k,v])=>[k,String(v)]));return readDiagnostics(await api(token,base+'diagnostics?'+query),context);}
   if(!data||!phase||!data.history.phases.some(p=>p.phase===phase&&columns.every(name=>p.columns.some(c=>c.name===name))))throw new Error('Выберите phase и native columns из descriptor');
   const selection=historySelection(start,stop,step,columns);
   return readHistory(await api(token,base+'history',{...pin,phase,selection}),context,data,phase,selection);
  },value=>{if(operation==='diagnostics'){setDescriptor({key,value:value as Diagnostics});setHistory(null);setPhase('');setColumns([]);}else setHistory({key,value:value as HistorySlice});setBusy('');},err=>{setError({key,message:errorMessage(err)});setBusy('');});
 }
 const phaseData=data?.history.phases.find(p=>p.phase===phase);
 return <section className="saved-diagnostics" aria-label="Сохранённая диагностика"><h4>Сохранённая диагностика</h4><p>Exact point {context.point_id}, source attempt {context.source_attempt}, provider attempt {context.selection.attempt}; commit {context.source_commit_sha256}. Только явное чтение/refresh, без polling, solve или materialize.</p>
 <button type="button" disabled={busy===key} onClick={()=>void load('diagnostics')}>Прочитать / обновить descriptor pinned commit</button>
 {busy===key&&<p role="status">Чтение bounded сохранённой диагностики…</p>}{error?.key===key&&<p role="alert">{error.message}</p>}
 {data&&<><DiagnosticSummary data={data}/>{data.history.status==='available'&&<><label>Native phase<select value={phase} onChange={e=>{invalidate();setPhase(e.target.value as 'scba'|'outer'|'');setColumns([]);}}><option value="">Выберите phase</option>{data.history.phases.map(p=><option key={p.phase} value={p.phase}>{p.phase} · {exactText(p.total_rows)} rows</option>)}</select></label>
 {phaseData&&<><fieldset><legend>1–16 native value columns; units как сохранены</legend>{phaseData.columns.map(column=><label key={column.name}><input type="checkbox" checked={columns.includes(column.name)} disabled={!columns.includes(column.name)&&columns.length>=16} onChange={e=>{invalidate();setColumns(v=>e.target.checked?[...v,column.name]:v.filter(n=>n!==column.name));}}/>{column.name} ({column.units??'unknown units'})</label>)}</fieldset>
 <div className="diagnostic-fields">{[['start','Start row ordinal',start,setStart],['stop','Stop exclusive',stop,setStop],['step','Step (пропуски разрывают линии)',step,setStep]].map(([name,label,value,setter])=><label key={String(name)}>{String(label)}<input value={value as string} inputMode="numeric" onChange={e=>{invalidate();(setter as (v:string)=>void)(e.target.value);}}/></label>)}</div>
 <button type="button" disabled={busy===key||!columns.length||!start||!stop||!step} onClick={()=>void load('history')}>Прочитать exact page (≤512 rows)</button></>}
 </>}</>}
 {slice&&<><p>Native {slice.phase}; selected {slice.coordinates.sequence.length} rows; omitted rows disconnected. Owner SHA проверен, scope: {exactText(slice.integrity.scope)}; full coordinate scan / science не заявляются.</p>
 <div className="diagnostic-table"><table><thead><tr><th>sequence</th><th>iteration</th><th>outer_iteration</th><th>source_attempt</th><th>break_before</th>{slice.columns.map(c=><th key={c.name}>{c.name} ({c.units??'unknown units'})</th>)}</tr></thead><tbody>{slice.coordinates.sequence.map((v,i)=><tr key={i}><td>{exactText(v)}</td><td>{exactText(slice.coordinates.iteration[i])}</td><td>{exactText(slice.coordinates.outer_iteration?.[i])}</td><td>{exactText(slice.coordinates.source_attempt[i])}</td><td>{String(slice.break_before[i])}</td>{slice.columns.map(c=><td key={c.name}>{c.values[i]===null?'native '+c.invalid_mask[i]:exactText(c.values[i])}</td>)}</tr>)}</tbody></table></div>
 <label>Optional plot X axis<select value={axis} onChange={e=>setAxis(e.target.value as 'iteration'|'sequence'|'')}><option value="">Без графика</option><option value="sequence">Native sequence</option><option value="iteration">Native iteration</option></select></label>
 <label>Optional plot column<select value={plotColumn} onChange={e=>setPlotColumn(e.target.value)}><option value="">Выберите column</option>{slice.columns.map(c=><option key={c.name} value={c.name}>{c.name} ({c.units??'unknown units'})</option>)}</select></label>
 {axis&&plotColumn&&<HistoryPlot data={slice} column={plotColumn} axis={axis}/>}
 <details><summary>Exact coverage / integrity / I/O counters</summary><pre>{JSON.stringify({coverage:slice.coverage,integrity:slice.integrity,io:slice.io_counters},null,2)}</pre></details></>}
 </section>;
}
