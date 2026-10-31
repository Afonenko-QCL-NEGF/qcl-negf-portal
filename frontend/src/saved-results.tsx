import {useEffect,useRef,useState} from 'react';
import {SavedDiagnostics} from './saved-diagnostics.tsx';
import {api,errorMessage} from './api';
import type {Run} from './api';
import {cellEdges,displayScale,readManifest,readSlice,nativePin,SavedSession,selectionOptions} from './saved-results-data';
import type {Masks,Numbers,SavedAxis,SavedSlice,SavedView,NativeContext,NativeEnvelope,SavedManifest} from './saved-results-data';

const show=(v:unknown)=>v==null?'unknown':typeof v==='object'?JSON.stringify(v):typeof v==='number'&&Object.is(v,-0)?'-0':String(v);
const number=(v:unknown):v is number=>typeof v==='number'&&Number.isFinite(v);
const colors=['#165da8','#b52c38','#25733d','#7952a2','#a45c00','#007b82','#733b3b','#576722','#a42477','#315b68','#6c4d99','#634c12'];
function GraphUnavailable(){return <p role="status">Graph unavailable: saved finite coordinates have no representable display geometry. Inspect the unchanged native value table.</p>;}
function Lines({paths,xlabel,ylabel,markersOnly=false,groups,labels,uncertain}:{paths:number[][][];xlabel:string;ylabel:string;markersOnly?:boolean;groups?:number[];labels?:string[];uncertain?:boolean[]}){
 const values=paths.flat();if(!values.length)return <p>No finite saved curve values in this page.</p>;
 let xs:ReturnType<typeof displayScale>,ys:ReturnType<typeof displayScale>;
 try{xs=displayScale(values.map(v=>v[0]));ys=displayScale(values.map(v=>v[1]));}
 catch{return <GraphUnavailable/>;}
 const x=(v:number)=>52+xs.position(v)*580,y=(v:number)=>252-ys.position(v)*216;
 try{for(const value of values)if(![x(value[0]),y(value[1])].every(number))throw new Error('geometry');}
 catch{return <GraphUnavailable/>;}
 const xmin=xs.minimum,xmax=xs.maximum,ymin=ys.minimum,ymax=ys.maximum;
 return <svg className="saved-plot" viewBox="0 0 680 300" role="img" aria-label={`${ylabel} versus ${xlabel}`}>
  <path d="M52 32V252H632" fill="none" stroke="#536477"/>
  <text x="52" y="275">{xmin.toPrecision(5)}</text><text x="565" y="275">{xmax.toPrecision(5)}</text>
  <text x="4" y="40">{ymax.toPrecision(4)}</text><text x="4" y="250">{ymin.toPrecision(4)}</text>
  <text x="300" y="295">{xlabel}</text><text x="60" y="20">{ylabel}</text>
  {paths.map((path,i)=><g key={i} stroke={colors[(groups?.[i]??i)%colors.length]} fill={colors[(groups?.[i]??i)%colors.length]}>
   <title>{labels?.[i]??(uncertain?.[i]?'unaccepted or unknown saved marker':'saved segment')}</title>
   {!markersOnly&&path.length>1&&<polyline fill="none" points={path.map(p=>`${x(p[0])},${y(p[1])}`).join(' ')}/>}
   {path.map((p,j)=>uncertain?.[i]?<path key={j} d={`M${x(p[0])},${y(p[1])-5}l5,5l-5,5l-5,-5z`} fill="#fff"><title>{`${show(p[0])}: ${show(p[1])} (unaccepted or unknown)`}</title></path>:<circle key={j} cx={x(p[0])} cy={y(p[1])} r="3"><title>{`${show(p[0])}: ${show(p[1])}`}</title></circle>)}
  </g>)}
 </svg>;
}
function axis(slice:SavedSlice,name:string):SavedAxis {const a=slice.axes.find(v=>v.name===name);if(!a)throw new Error('Native axis unavailable');return a;}
function Heatmap({slice}:{slice:SavedSlice}){
 const vertical=axis(slice,slice.logical_axes[0]),horizontal=axis(slice,slice.logical_axes[1]);
 let xs:number[],ys:number[],pixelX:number[],pixelY:number[];
 try{
  xs=cellEdges(horizontal.values);ys=cellEdges(vertical.values);
  const sx=displayScale(xs),sy=displayScale(ys);
  pixelX=xs.map(v=>60+sx.position(v)*560);pixelY=ys.map(v=>245-sy.position(v)*210);
  if(!pixelX.every(number)||!pixelY.every(number))throw new Error('geometry');
 }catch{return <GraphUnavailable/>;}
 const values=slice.values as (number|null)[][],mask=slice.invalid_mask as (string|null)[][];
 const finite=values.flat().filter(number);const maximum=Math.max(...finite.map(Math.abs),1e-300);
 return <><svg className="saved-plot" viewBox="0 0 680 300" role="img" aria-label={`${slice.view_id} saved native cells`}>
  {values.map((row,i)=>row.map((v,j)=>{
   const strength=v===null?0:Math.abs(v)/maximum;
   const pale=Math.round(255-180*strength),fill=v===null?'#d7dde5':v<0?`rgb(${pale},${pale},255)`:`rgb(255,${pale},${pale})`;
   return <rect key={`${i}:${j}`} x={pixelX[j]} y={pixelY[i+1]} width={pixelX[j+1]-pixelX[j]} height={pixelY[i]-pixelY[i+1]} fill={fill} stroke={v===null?'#536477':'#fff'} strokeDasharray={v===null?'3 2':undefined}><title>{`${vertical.name}=${vertical.values[i]}, ${horizontal.name}=${horizontal.values[j]}: ${v===null?'native '+mask[i][j]:`${show(v)} ${slice.units}`}`}</title></rect>;
  }))}
  <text x="280" y="295">{horizontal.name} ({horizontal.units})</text><text x="60" y="20">{vertical.name} ({vertical.units})</text>
  <text x="60" y="265">{horizontal.values[0]}</text><text x="580" y="265">{horizontal.values.at(-1)}</text>
  <text x="4" y="40">{vertical.values.at(-1)}</text><text x="4" y="245">{vertical.values[0]}</text>
 </svg><p className="muted">Signed blue/white/red scale: −{maximum} … 0 … {maximum} {slice.units}. Grey outlined cells retain native invalid reasons. Cell boundaries lie midway between saved coordinate centers.</p></>;
}
function FlatTable({values,mask,axes,units}:{values:Numbers;mask:Masks;axes:SavedAxis[];units:string}){
 const rows:{coordinates:string;value:string}[]=[];
 function visit(v:Numbers,m:Masks,index:number[]){if(Array.isArray(v)){v.forEach((x,i)=>visit(x,(m as Masks[])[i],[...index,i]));}else rows.push({coordinates:index.map((n,i)=>`${axes[i]?.name??i}=${axes[i]?.values[n]??n}`).join(', ')||'scalar',value:v===null?`native ${m}`:`${show(v)} ${units}`});}
 visit(values,mask,[]);
 const [limit,setLimit]=useState(128);
 return <><div className="saved-table"><table><thead><tr><th>Saved coordinates</th><th>Value / native diagnostic</th></tr></thead><tbody>{rows.slice(0,limit).map((r,i)=><tr key={i}><td>{r.coordinates}</td><td>{r.value}</td></tr>)}</tbody></table></div>{limit<rows.length&&<button type="button" onClick={()=>setLimit(n=>n+128)}>Show next 128 native cells ({rows.length} total)</button>}</>;
}
function PopulationBars({slice,coordinate}:{slice:SavedSlice;coordinate:SavedAxis}){
 const values=slice.values as (number|null)[],mask=slice.invalid_mask as (string|null)[];
 const finite=values.filter(number);
 let scale:ReturnType<typeof displayScale>;
 try{scale=displayScale([0,...finite]);}catch{return <GraphUnavailable/>;}
 const y=(v:number)=>245-scale.position(v)*210;
 try{if(![0,...finite].map(y).every(number))throw new Error('geometry');}
 catch{return <GraphUnavailable/>;}
 const width=560/Math.max(values.length,1);
 return <svg className="saved-plot" viewBox="0 0 680 300" role="img" aria-label="Saved state populations m^-2">
 <path d={`M60 ${y(0)}H620`} stroke="#536477"/>
 <text x="60" y="20">Saved {slice.basis} populations ({slice.units})</text>
 {values.map((v,i)=><g key={i}>{v!==null&&<rect x={60+i*width+width*.15} y={Math.min(y(0),y(v))} width={width*.7} height={Math.abs(y(v)-y(0))} fill={v<0?'#165da8':'#a42c38'}><title>{`state ${coordinate.values[i]}: ${show(v)} ${slice.units}`}</title></rect>}<text x={60+i*width} y="275">{coordinate.values[i]}{v===null?`: ${mask[i]}`:''}</text></g>)}
 <text x="280" y="295">Saved state index</text></svg>;
}
export function SavedPlot({slice}:{slice:SavedSlice}){
 const logical=slice.logical_axes.map(name=>axis(slice,name));
 let plot;
 if(slice.returned_shape.length===1){
  const values=slice.values as (number|null)[],mask=slice.invalid_mask as (string|null)[];
  const segments:number[][][]=[];let line:number[][]=[];
  values.forEach((v,i)=>{if(v===null||mask[i]!==null){if(line.length)segments.push(line);line=[];}else line.push([logical[0].values[i],v]);});if(line.length)segments.push(line);
  plot=slice.view_id.endsWith('_populations')?<PopulationBars slice={slice} coordinate={logical[0]}/>:<Lines paths={segments} xlabel={`${logical[0].name} (${logical[0].units})`} ylabel={`${slice.view_id} (${slice.units})`} markersOnly={slice.view_id.endsWith('_levels')||slice.view_id.endsWith('_populations')}/>;
 }else if(slice.returned_shape.length===2&&slice.view_id.includes('_wavefunctions_')){
  const values=slice.values as (number|null)[][],mask=slice.invalid_mask as (string|null)[][];
  const paths:number[][][]=[],labels:string[]=[],groups:number[]=[];
  for(let state=0;state<slice.returned_shape[1];state++){
   const save=(line:number[][])=>{paths.push(line);labels.push(`state ${logical[1].values[state]}`);groups.push(state);};let active:number[][]=[];
   values.forEach((row,i)=>{if(row[state]===null||mask[i][state]!==null){if(active.length)save(active);active=[];}else active.push([logical[0].values[i],row[state]!]);});if(active.length)save(active);
  }
  plot=<><Lines paths={paths} labels={labels} groups={groups} xlabel={`${logical[0].name} (${logical[0].units})`} ylabel={`${slice.view_id} (${slice.units})`}/><p>{labels.join('; ')}. Saved gauge: {show(slice.conventions.gauge)}.</p></>;
 }else if(slice.returned_shape.length===2)plot=<Heatmap slice={slice}/>;
 return <div className="saved-view">{plot}
 {slice.view_id==='spatial_energy_density'&&<p>Saved n(E,z), m^-3/eV. This is spatial-energy density; cell colors preserve its sign.</p>}
 {(slice.view_id==='spectral_trace'||slice.view_id==='occupied_trace')&&<p>Saved spectral trace at the explicitly selected k index/window, eV^-1. Native momentum coordinates and weights remain disclosed.</p>}
 <FlatTable values={slice.values} mask={slice.invalid_mask} axes={logical} units={slice.units}/>
 {slice.view_id.endsWith('_levels')&&<p>Saved projected k=0 Hamiltonian levels; these are not interacting spectral peaks.</p>}
 {slice.view_id.endsWith('_populations')&&<><p>Saved {slice.basis} populations, m^-2; normalization is supplied only by Results from saved ionized target doping.</p><pre>{show(slice.fractions)}</pre></>}
 <details><summary>Native axes, weights, basis and conventions</summary><pre>{JSON.stringify({axes:slice.axes,basis:slice.basis,conventions:slice.conventions,integrity:slice.integrity,source:slice.source},null,2)}</pre></details>
 <p>Raw recorded statuses (no new assessment):</p><pre>{show(slice.recorded_statuses)}</pre></div>;
}
function nativeSelection(view:SavedView,fields:Record<string,string>):(number|{start:number;stop:number;step:number})[]{
 return Array.from({length:view.allowed_selection!.axis_count},(_,i)=>{
  const integer=(key:string)=>{const raw=fields[`${i}:${key}`]??'';if(!/^(0|[1-9][0-9]*)$/.test(raw))throw new Error('Enter explicit nonnegative native indices/ranges');const n=Number(raw);if(!Number.isSafeInteger(n))throw new Error('Index exceeds finite bound');return n;};
  if(fields[`${i}:mode`]==='index')return integer('index');
  const start=integer('start'),stop=integer('stop'),step=integer('step');if(stop<=start||step<1)throw new Error('Require stop > start and step > 0');return {start,stop,step};
 });
}
export function SavedResultsPanel({token,run}:{token:string;run:Run}){
 const options=selectionOptions(run),session=useRef(new SavedSession()),identity=useRef('');
 const [choice,setChoice]=useState(''),[pointId,setPointId]=useState(''),[binding,setBinding]=useState(''),[manifestSHA,setManifestSHA]=useState('');
 const [stored,setStored]=useState<{key:string;value:NativeEnvelope<SavedManifest>}|null>(null),[slice,setSlice]=useState<{key:string;value:SavedSlice}|null>(null);
 const [viewId,setViewId]=useState(''),[fields,setFields]=useState<Record<string,string>>({}),[error,setError]=useState(''),[busy,setBusy]=useState(''),[diagnostics,setDiagnostics]=useState(false);
 const selection=options.find(v=>v.calcjob_uuid===choice),points=(run.plan_points??[]).filter(p=>p.execution_id===selection?.execution_id);
 const point=points.find(p=>p.id===pointId),key=JSON.stringify([token,run.uuid,choice,pointId,binding,manifestSHA]);
 if(identity.current!==key){session.current.invalidate();identity.current=key;}
 const manifest=stored?.key===key?stored.value:null,shown=slice?.key===key?slice.value:null,view=manifest?.view.views[viewId];
 const context:NativeContext|undefined=selection&&point?{selection,point_id:pointId,native_binding_uuid:binding,state_manifest_sha256:manifestSHA,...(manifest?{state_ref:manifest.source_state}:{})}:undefined;
 function invalidate(){session.current.invalidate();setSlice(null);setError('');setBusy('');}
 function reset(){invalidate();setStored(null);setViewId('');setFields({});setDiagnostics(false);}
 useEffect(()=>{reset();return()=>session.current.invalidate();},[key]);
 async function load(operation:'manifest'|'slice'){
  if(!context)return;const selected=context,g=session.current.begin();setBusy(key);setError('');
  await session.current.receive(g,async()=>{
   const pin=nativePin(selected),base=`/runs/${encodeURIComponent(run.uuid)}/saved-results/`;
   if(operation==='manifest'){const query=new URLSearchParams(Object.entries(pin).map(([k,v])=>[k,String(v)]));return readManifest(await api(token,base+'manifest?'+query),selected);}
   if(!manifest||!view)throw new Error('Select an available native view');
   return readSlice(await api(token,base+'slice',{...pin,source_state:manifest.source_state,view_id:viewId,selection:nativeSelection(view,fields)}),selected,viewId);
  },value=>{if(operation==='manifest'){setStored({key,value:value as NativeEnvelope<SavedManifest>});setSlice(null);setViewId('');setFields({});}else setSlice({key,value:(value as NativeEnvelope).view as unknown as SavedSlice});setBusy('');},e=>{setError(errorMessage(e));setBusy('');});
 }
 return <section className="saved-results"><h3>Сохранённый транспорт native5</h3>
 <p>Explicit retained point: run → execution / transport attempt / CalcJob → original binding UUID → exact manifest SHA. Native attempt and AiiDA attempt are separate identities. No solver or derive starts.</p>
 <p>Execution-wide brief/catalog (R1), current history (R3), retrieved payload (R4) and performance producer (R5) remain unavailable pending their owning contracts.</p>
 <label>Execution / attempt / exact CalcJob<select value={choice} onChange={e=>{reset();setChoice(e.target.value);setPointId('');setBinding('');setManifestSHA('');}}><option value="">Select explicitly</option>{options.map(v=><option key={v.calcjob_uuid} value={v.calcjob_uuid}>{v.execution_id} · attempt {v.attempt} · {v.calcjob_uuid}</option>)}</select></label>
 <label>Exact frozen point<select value={pointId} onChange={e=>{reset();setPointId(e.target.value);setBinding('');setManifestSHA('');}}><option value="">Select point explicitly</option>{points.map(p=><option key={p.id} value={p.id}>{p.id} · {p.temperature_K} K · {p.voltage_per_period_V} V</option>)}</select></label>
 {!points.length&&<p>Exact frozen point inventory unavailable for this execution.</p>}
 <label>Original native binding UUID<input value={binding} onChange={e=>{reset();setBinding(e.target.value);}}/></label>
 <label>Selected native manifest SHA-256<input value={manifestSHA} onChange={e=>{reset();setManifestSHA(e.target.value);}}/></label>
 <p>Copy exact retained pins from the owning provenance record. Input claims grant no filesystem access; the owner rechecks them. No first/latest binding is inferred.</p>
 <button type="button" disabled={!context||busy===key||!binding||!manifestSHA} onClick={()=>void load('manifest')}>Describe exact retained views</button>
 <button type="button" disabled={!context||busy===key||!binding||!manifestSHA} onClick={()=>setDiagnostics(true)}>Сохранённая диагностика (checkpoint/history, без final view)</button>
 {error&&<p role="alert" className="error">{error}</p>}{busy===key&&<p role="status">Reading bounded retained selection…</p>}
 {diagnostics&&context&&<SavedDiagnostics key={key} token={token} context={context}/>}
 {manifest&&<><p>Readback integrity: native contexts not reverified; science not assessed. Original binding integrity: historical selected metadata verified; current access not checked. RO access is a separate current-request observation.</p><details><summary>Exact native state, original provenance, current request access and raw assessments</summary><pre>{JSON.stringify(manifest,null,2)}</pre></details>
 <label>Saved semantic view<select value={viewId} onChange={e=>{invalidate();setViewId(e.target.value);setFields({});}}><option value="">Select a view</option>{Object.values(manifest.view.views).map(v=><option key={v.view_id} value={v.view_id} disabled={v.availability!=='available'}>{v.view_id} · {v.units} · {v.availability} {v.issue?JSON.stringify(v.issue):''}</option>)}</select></label></>}
 {view?.availability==='available'&&<><p>Native shape {view.shape?.join(' × ')}; {view.basis??'no basis'}; {view.units}. Explicit native indices/ranges only.</p>
 {Array.from({length:view.allowed_selection!.axis_count},(_,i)=><fieldset key={`${viewId}:${i}`}><legend>{view.axes?.[i]} native axis</legend>
 <select aria-label={`Axis ${i} selection mode`} value={fields[`${i}:mode`]??'range'} onChange={e=>{invalidate();setFields(f=>({...f,[`${i}:mode`]:e.target.value}));}}><option value="range">Explicit range</option><option value="index">Explicit index</option></select>
 {(fields[`${i}:mode`]==='index'?['index']:['start','stop','step']).map(k=><label key={k}>{k}{k==='stop'?' (exclusive)':''}<input value={fields[`${i}:${k}`]??''} onChange={e=>{invalidate();setFields(f=>({...f,[`${i}:${k}`]:e.target.value}));}}/></label>)}</fieldset>)}
 <button type="button" disabled={busy===key} onClick={()=>void load('slice')}>Read exact native selection</button></>}
 {shown&&<SavedPlot slice={shown}/>}</section>;
}
