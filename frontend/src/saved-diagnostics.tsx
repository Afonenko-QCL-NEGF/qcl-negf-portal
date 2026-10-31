import {useEffect,useRef,useState} from 'react';
import {api,errorMessage} from './api.ts';
import {SavedSession,displayScale,nativePin} from './saved-results-data.ts';
import type {NativeEnvelope} from './saved-results-data.ts';
import {exactText,historyPaths,historyRequest,readDiagnostics,readHistory} from './saved-diagnostics-data.ts';
import type {DiagnosticContext,Diagnostics,HistorySlice} from './saved-diagnostics-data.ts';
export function HistoryPlot({data,column}:{data:HistorySlice;column:string}){
 const paths=historyPaths(data,column),values=paths.flat();if(!values.length)return <p>Нет конечной representable curve; exact tagged values и coordinates остаются в таблице.</p>;
 let x:(v:number)=>number,y:(v:number)=>number;
 try{const sx=displayScale(values.map(v=>v[0])),sy=displayScale(values.map(v=>v[1]));x=v=>50+sx.position(v)*580;y=v=>240-sy.position(v)*200;for(const p of values)if(!Number.isFinite(x(p[0]))||!Number.isFinite(y(p[1])))throw new Error('geometry');}catch{return <p>Геометрия графика недоступна; исходная таблица сохранена.</p>;}
 return <svg className="diagnostic-plot" viewBox="0 0 680 290" role="img" aria-label={`${column} versus native sequence`}><path d="M50 25V240H630" stroke="#546b82" fill="none"/><text x="280" y="280">Native sequence</text><text x="50" y="18">{column}; units в исходной таблице</text>{paths.map((path,i)=><g key={i} stroke="#215f96" fill="#215f96">{path.length>1&&<polyline fill="none" points={path.map(p=>`${x(p[0])},${y(p[1])}`).join(' ')}/>} {path.map((p,j)=><circle key={j} cx={x(p[0])} cy={y(p[1])} r="3"><title>{`${p[0]}: ${p[1]}`}</title></circle>)}</g>)}</svg>;
}
export function DiagnosticSummary({data}:{data:Diagnostics}){return <div><p>Closed: {exactText(data.closed)}. Retained pinned source; не live. Process, convergence, physics, discretization и experimental status остаются отдельными исходными axes.</p><pre>{JSON.stringify({status:data.status,history:data.history,resources:data.resources,provenance:data.provenance,io_counters:data.io_counters},null,2)}</pre><p>Units до чтения record остаются not_loaded; scientific acceptance не выводится из формы кривой.</p></div>;}
export function SavedDiagnostics({token,context}:{token:string;context:DiagnosticContext}){
 const session=useRef(new SavedSession()),identity=useRef(''),key=JSON.stringify([token,context]);if(identity.current!==key){session.current.invalidate();identity.current=key;}
 const [descriptor,setDescriptor]=useState<{key:string;value:NativeEnvelope<Diagnostics>}|null>(null),[history,setHistory]=useState<{key:string;value:HistorySlice}|null>(null);
 const [stage,setStage]=useState<'scba'|'outer'|''>(''),[fields,setFields]=useState<string[]>([]),[maximum,setMaximum]=useState(''),[column,setColumn]=useState(''),[busy,setBusy]=useState(''),[error,setError]=useState('');
 const data=descriptor?.key===key?descriptor.value:null,slice=history?.key===key?history.value:null;
 useEffect(()=>{session.current.invalidate();setDescriptor(null);setHistory(null);setStage('');setFields([]);setMaximum('');setColumn('');setBusy('');setError('');return()=>session.current.invalidate();},[key]);
 function invalidate(){session.current.invalidate();setHistory(null);setBusy('');setError('');}
 async function load(operation:'diagnostics'|'history',next=false){
  const g=session.current.begin();setBusy(key);setError('');
  await session.current.receive(g,async()=>{const pin=nativePin(context),base=`/runs/${encodeURIComponent(context.selection.run_uuid)}/saved-results/`;
   if(operation==='diagnostics'){const query=new URLSearchParams(Object.entries(pin).map(([k,v])=>[k,String(v)]));return readDiagnostics(await api(token,base+'diagnostics?'+query),context);}
   if(!data||!stage||!fields.every(f=>f in data.view.fields[stage]))throw new Error('Choose stage/direct fields explicitly');
   const request=historyRequest(stage,fields,maximum,next?slice?.cursor??null:null),bound={...context,state_ref:data.source_state};
   return readHistory(await api(token,base+'history',{...pin,source_state:data.source_state,...request}),bound,request);
  },value=>{if(operation==='diagnostics'){setDescriptor({key,value:value as NativeEnvelope<Diagnostics>});setHistory(null);setStage('');setFields([]);}else setHistory({key,value:(value as NativeEnvelope).view as unknown as HistorySlice});setBusy('');},err=>{setError(errorMessage(err));setBusy('');});
 }
 return <section className="saved-diagnostics" aria-label="Сохранённая диагностика"><h4>Сохранённая диагностика native5</h4><p>Exact point {context.point_id}; transport attempt {context.selection.attempt}; native identity берётся из source StateRef. Только explicit retained reads, без polling/solve/derive.</p>
 <button type="button" disabled={busy===key} onClick={()=>void load('diagnostics')}>Прочитать descriptor pinned StateRef</button>{busy===key&&<p role="status">Чтение bounded native history…</p>}{error&&<p role="alert">{error}</p>}
 {data&&<><p>Readback integrity: native contexts not reverified; science not assessed. Original binding integrity: historical selected metadata verified; current access not checked. RO access is a separate current-request observation.</p><details><summary>Original binding and readback integrity / request access</summary><pre>{JSON.stringify({retained:data.retained,access:data.access},null,2)}</pre></details><DiagnosticSummary data={data.view}/><label>Native stage<select value={stage} onChange={e=>{invalidate();setStage(e.target.value as 'scba'|'outer'|'');setFields([]);}}><option value="">Select explicitly</option><option value="scba">scba</option><option value="outer">outer</option></select></label>
 {stage&&<><fieldset><legend>Direct stored IEEE fields (≤16)</legend>{Object.keys(data.view.fields[stage]).map(field=><label key={field}><input type="checkbox" checked={fields.includes(field)} disabled={!fields.includes(field)&&fields.length>=16} onChange={e=>{invalidate();setFields(v=>e.target.checked?[...v,field]:v.filter(f=>f!==field));}}/>{field}</label>)}</fieldset><label>Maximum consumed events (1–256)<input value={maximum} inputMode="numeric" onChange={e=>{invalidate();setMaximum(e.target.value);}}/></label>
 <button type="button" disabled={busy===key||!fields.length||!maximum} onClick={()=>void load('history')}>Read first pinned batch</button></>}</>}
 {slice&&<><p>Consumed {slice.records.length} events; {slice.rows.length} displayed rows; {slice.witness_updates.length} witness updates retained separately. At frozen cutoff: {String(slice.at_cutoff)}.</p>
 <button type="button" disabled={busy===key||slice.at_cutoff} onClick={()=>void load('history',true)}>Next exact cursor batch</button>
 <div className="diagnostic-table"><table><thead><tr><th>Stream ordinal</th><th>Sequence / row key</th><th>Break</th>{fields.map(f=><th key={f}>{f} / units / original IEEE</th>)}</tr></thead><tbody>{slice.rows.map((row,i)=><tr key={i}><td>{row.stream_ordinal}</td><td>{exactText(row.sequence)}<pre>{JSON.stringify(row.row_key)}</pre></td><td>{String(row.break_before)}</td>{fields.map(f=><td key={f}>{row.cells[f].value===null?'native '+row.cells[f].invalid_reason:exactText(row.cells[f].value)} {row.cells[f].units??'unknown units'}<pre>{JSON.stringify({tagged:row.cells[f].tagged,missing_reason:row.cells[f].missing_reason})}</pre></td>)}</tr>)}</tbody></table></div>
 <label>Optional plot<select value={column} onChange={e=>setColumn(e.target.value)}><option value="">Without graph</option>{fields.map(f=><option key={f} value={f}>{f}</option>)}</select></label>{column&&<HistoryPlot data={slice} column={column}/>}
 <details><summary>Original records / witness updates / cursor / I/O</summary><pre>{JSON.stringify(slice,null,2)}</pre></details></>}
 </section>;
}
