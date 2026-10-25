import {useEffect,useRef,useState} from 'react';
import {api,errorMessage} from './api';
import type {Run} from './api';
import {cellEdges,currentPaths,displayScale,readBrief,readManifest,readSlice,readSources,retrievedSource,SavedSession,selectionOptions} from './saved-results-data';
import type {Masks,Numbers,SavedAxis,SavedBrief,SavedManifest,SavedPoint,SavedSelection,SavedSlice,SavedView,SavedProviderSource,SavedSources} from './saved-results-data';

const show=(v:unknown)=>v==null?'unknown':typeof v==='object'?JSON.stringify(v):String(v);
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
   {path.map((p,j)=>uncertain?.[i]?<path key={j} d={`M${x(p[0])},${y(p[1])-5}l5,5l-5,5l-5,-5z`} fill="#fff"><title>{`${p[0]}: ${p[1]} (unaccepted or unknown)`}</title></path>:<circle key={j} cx={x(p[0])} cy={y(p[1])} r="3"><title>{`${p[0]}: ${p[1]}`}</title></circle>)}
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
   return <rect key={`${i}:${j}`} x={pixelX[j]} y={pixelY[i+1]} width={pixelX[j+1]-pixelX[j]} height={pixelY[i]-pixelY[i+1]} fill={fill} stroke={v===null?'#536477':'#fff'} strokeDasharray={v===null?'3 2':undefined}><title>{`${vertical.name}=${vertical.values[i]}, ${horizontal.name}=${horizontal.values[j]}: ${v===null?'native '+mask[i][j]:`${v} ${slice.units}`}`}</title></rect>;
  }))}
  <text x="280" y="295">{horizontal.name} ({horizontal.units})</text><text x="60" y="20">{vertical.name} ({vertical.units})</text>
  <text x="60" y="265">{horizontal.values[0]}</text><text x="580" y="265">{horizontal.values.at(-1)}</text>
  <text x="4" y="40">{vertical.values.at(-1)}</text><text x="4" y="245">{vertical.values[0]}</text>
 </svg><p className="muted">Signed blue/white/red scale: −{maximum} … 0 … {maximum} {slice.units}. Grey outlined cells retain native invalid reasons. Cell boundaries lie midway between saved coordinate centers.</p></>;
}
function FlatTable({values,mask,axes,units}:{values:Numbers;mask:Masks;axes:SavedAxis[];units:string}){
 const rows:{coordinates:string;value:string}[]=[];
 function visit(v:Numbers,m:Masks,index:number[]){if(Array.isArray(v)){v.forEach((x,i)=>visit(x,(m as Masks[])[i],[...index,i]));}else rows.push({coordinates:index.map((n,i)=>`${axes[i]?.name??i}=${axes[i]?.values[n]??n}`).join(', ')||'scalar',value:v===null?`native ${m}`:`${v} ${units}`});}
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
 {values.map((v,i)=><g key={i}>{v!==null&&<rect x={60+i*width+width*.15} y={Math.min(y(0),y(v))} width={width*.7} height={Math.abs(y(v)-y(0))} fill={v<0?'#165da8':'#a42c38'}><title>{`state ${coordinate.values[i]}: ${v} ${slice.units}`}</title></rect>}<text x={60+i*width} y="275">{coordinate.values[i]}{v===null?`: ${mask[i]}`:''}</text></g>)}
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
function CurrentVoltage({brief}:{brief:SavedBrief}){
 const paths=currentPaths(brief);const points=brief.points.filter(p=>number(p.coordinates.voltage_per_period_V)&&number(p.scalar_observables.current_density_A_per_m2));
 // Owning Results supplies connectable segments. Isolated unaccepted/unknown
 // markers remain visible without adding connecting lines through gaps.
 const connected=new Set(brief.current_segments.flat());
 const extra=points.filter(p=>!connected.has(p.point_id)).map(p=>[[Number(p.coordinates.voltage_per_period_V),Number(p.scalar_observables.current_density_A_per_m2)]]);
 return <><h4>Saved J(V)</h4><Lines paths={[...paths,...extra]} uncertain={[...paths.map(()=>false),...extra.map(()=>true)]} xlabel="V per period (V)" ylabel="Current density (A/m²)"/>
 <p>Positive electron flow toward +z. Branch/execution order and gaps come from saved metadata. Unaccepted/unknown points have isolated diamond markers. Page boundaries are not joined.</p>
 <div className="saved-table"><table><thead><tr><th>Point / branch / T (K)</th><th>V/period (V)</th><th>J (A/m²)</th><th>Raw recorded status / assessment</th></tr></thead><tbody>{brief.points.map(p=><tr key={p.point_id}><td>{p.point_id} / {show(p.coordinates.branch)} / {show(p.coordinates.temperature_K)}</td><td>{show(p.coordinates.voltage_per_period_V)}</td><td>{show(p.scalar_observables.current_density_A_per_m2)}</td><td>{show(p.recorded_statuses)} {p.missing.join(', ')}</td></tr>)}</tbody></table></div></>;
}
function nativeSelection(view:SavedView,fields:Record<string,string>):(number|{start:number;stop:number;step:number})[]{
 return Array.from({length:view.allowed_selection!.axis_count},(_,i)=>{
  const integer=(key:string)=>{const raw=fields[`${i}:${key}`]??'';if(!/^(0|[1-9][0-9]*)$/.test(raw))throw new Error('Enter explicit nonnegative native indices/ranges');const n=Number(raw);if(!Number.isSafeInteger(n))throw new Error('Index exceeds finite bound');return n;};
  if(fields[`${i}:mode`]==='index')return integer('index');
  const start=integer('start'),stop=integer('stop'),step=integer('step');if(stop<=start||step<1)throw new Error('Require stop > start and step > 0');return {start,stop,step};
 });
}
export function SavedResultsPanel({token,run}:{token:string;run:Run}){
 const options=selectionOptions(run),session=useRef(new SavedSession());
 const [choice,setChoice]=useState(''),[brief,setBrief]=useState<SavedBrief|null>(null),[point,setPoint]=useState<SavedPoint|null>(null);
 const [manifest,setManifest]=useState<SavedManifest|null>(null),[viewId,setViewId]=useState(''),[slice,setSlice]=useState<SavedSlice|null>(null);
 const [provider,setProvider]=useState(''),[sources,setSources]=useState<SavedSources|null>(null),[sourceChoice,setSourceChoice]=useState('');
 const registered=sources?.sources.find(s=>s.source_uuid===sourceChoice);
 const source:SavedProviderSource|undefined=provider==='aiida_retrieved'?retrievedSource:provider==='nfs_registered'&&registered?{provider:'nfs_registered',source_uuid:registered.source_uuid,source_sha256:registered.source_sha256}:undefined;
 const [fields,setFields]=useState<Record<string,string>>({}),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const selection:SavedSelection|undefined=options.find(v=>`${v.execution_id}/${v.attempt}/${v.calcjob_uuid}`===choice);
 useEffect(()=>{reset();setSources(null);setSourceChoice('');return()=>session.current.invalidate();},[token,run.uuid,selection?.execution_id,selection?.attempt,selection?.calcjob_uuid,provider]);
 useEffect(()=>{reset();},[source?.provider,source?.source_uuid,source?.source_sha256]);
 const view=manifest?.views.find(v=>v.view_id===viewId);
 function invalidate(){session.current.invalidate();setBusy(false);setError('');setSlice(null);}
 function reset(){invalidate();setBrief(null);setPoint(null);setManifest(null);setViewId('');setFields({});}
 function resetSource(){reset();setSources(null);setSourceChoice('');}
 async function load(kind:'sources'|'brief'|'manifest'|'slice',cursor?:string){
  if(!selection||(kind!=='sources'&&!source))return;const selected=selection,pinned=source,id=session.current.begin();setBusy(true);setError('');
  await session.current.receive(id,async()=>{
   const base=`/runs/${encodeURIComponent(run.uuid)}/saved-results/`;
   const exact={execution_id:selected.execution_id,attempt:selected.attempt,calcjob_uuid:selected.calcjob_uuid};
   if(kind==='sources'){const query=new URLSearchParams({...exact,attempt:String(exact.attempt)});return readSources(await api(token,base+'sources?'+query),selected);}
   if(!pinned)throw new Error('Select an exact source');
   const context={...exact,provider:pinned.provider,...(pinned.provider==='nfs_registered'?{source_uuid:pinned.source_uuid!,source_sha256:pinned.source_sha256!}:{})};
   if(kind==='brief'){const query=new URLSearchParams(Object.entries({...context,...(cursor?{cursor}:{})}).map(([k,v])=>[k,String(v)]));return readBrief(await api(token,base+'brief?'+query),selected,pinned);}
   if(!point||!brief||!point.source_commit_sha256)throw new Error('Select a saved point and captured commit');
   const pin={...context,point_id:point.point_id,source_commit_sha256:point.source_commit_sha256,metadata_sha256:brief.metadata_sha256};
   if(kind==='manifest'){const query=new URLSearchParams(Object.entries(pin).map(([k,v])=>[k,String(v)]));return readManifest(await api(token,base+'manifest?'+query),selected,point.point_id,point.source_commit_sha256,brief.metadata_sha256,pinned,point);}
   if(!view)throw new Error('Select an available semantic view');
   return readSlice(await api(token,base+'slice',{...pin,view_id:viewId,selection:nativeSelection(view,fields)}),selected,point.point_id,point.source_commit_sha256,viewId,brief.metadata_sha256,pinned,manifest??undefined);
  },value=>{if(kind==='sources'){setSources(value as SavedSources);}else if(kind==='brief'){setBrief(value as SavedBrief);setPoint(null);setManifest(null);setViewId('');setSlice(null);}else if(kind==='manifest'){setManifest(value as SavedManifest);setViewId('');setSlice(null);}else setSlice(value as SavedSlice);setBusy(false);},e=>{setError(errorMessage(e));setBusy(false);});
 }
 return <section className="saved-results"><h3>Сохранённый транспорт</h3><p>Explicit run → execution/variant → attempt/CalcJob → provider/source → point/commit. Registered metadata and native payload integrity are distinct from scientific acceptance. No new solver starts.</p>
 <label>Execution / attempt / exact CalcJob<select value={choice} onChange={e=>{resetSource();setChoice(e.target.value);}}><option value="">Select explicitly</option>{options.map(v=><option key={v.calcjob_uuid} value={`${v.execution_id}/${v.attempt}/${v.calcjob_uuid}`}>{v.execution_id} · attempt {v.attempt} · {v.calcjob_uuid}</option>)}</select></label>
 {!options.length&&<p>No exact retained attempt inventory is available.</p>}
 <label>Saved provider<select value={provider} onChange={e=>{resetSource();setProvider(e.target.value);}}><option value="">Select provider explicitly</option><option value="aiida_retrieved">AiiDA retrieved repository</option><option value="nfs_registered">Registered retained NFS source</option></select></label>
 {provider==='nfs_registered'&&<><button type="button" disabled={!selection||busy} onClick={()=>void load('sources')}>Read registered sources</button>
 {sources&&<><p>Registration metadata verified; storage binding {sources.storage_binding_status}; payload access not checked. {sources.sources.length} retained sources; complete catalogue.</p>
 <label>Registered source UUID / SHA<select value={sourceChoice} onChange={e=>{reset();setSourceChoice(e.target.value);}}><option value="">Select source explicitly</option>{sources.sources.map(s=><option key={s.source_uuid} value={s.source_uuid}>{s.source_uuid} · {s.source_sha256}</option>)}</select></label>
 {!sources.sources.length&&<p>No registered source for this exact attempt.</p>}{registered&&<p>Metadata {registered.metadata_status}; copy status {show(registered.copy_status)}; access {registered.access_status}.</p>}</>}</>}

 <button type="button" disabled={!selection||!source||busy} onClick={()=>void load('brief')}>Read saved metadata</button>
 {error&&<p role="alert" className="error">{error}</p>}{busy&&<p role="status">Reading bounded saved selection…</p>}
 {brief&&<><p>Source: {brief.provider_source.provider}; source UUID {show(brief.provider_source.source_uuid)}; source SHA {show(brief.provider_source.source_sha256)}; run {run.uuid}; definition {show(brief.identity.definition_id)}; variant {show(brief.identity.variant_id)}. Metadata SHA {brief.metadata_sha256}. Page {brief.points.length}/{brief.total}; {brief.complete_page?'complete set':'partial page'}.</p>
 {brief.provider_source.provider==='nfs_registered'&&<p>Registered snapshot complete: {show(brief.snapshot_complete)}. Storage {brief.storage_binding_status}; native payload access remains unmeasured until requested.</p>}
 <p>Raw series statuses: {show(brief.statuses)}. Discretization and experimental validation remain unknown unless recorded in source.</p>
 <details><summary>Exact saved provenance and raw assessments</summary><pre>{JSON.stringify({selection:brief.provider_selection,identity:brief.identity,source:brief.source,provider_source:brief.provider_source,statuses:brief.statuses},null,2)}</pre></details>
 <CurrentVoltage brief={brief}/>{brief.next_cursor&&<button type="button" disabled={busy} onClick={()=>void load('brief',brief.next_cursor!)}>Next pinned page</button>}
 <label>Saved point / commit<select value={point?.point_id??''} onChange={e=>{invalidate();setPoint(brief.points.find(p=>p.point_id===e.target.value)??null);setManifest(null);setViewId('');setFields({});}}><option value="">Select explicitly</option>{brief.points.map(p=><option key={p.point_id} value={p.point_id} disabled={!p.source_commit_sha256}>{p.point_id} · source attempt {show(p.source_attempt)} · {p.analysis_source_status??'repository'} · {p.source_commit_sha256??'commit unavailable'}</option>)}</select></label>
 {point&&<p>Conditions {show(point.coordinates)}; commit {point.source_commit_sha256}; source attempt {show(point.source_attempt)}; provider attempt {selection?.attempt}. Analysis source {point.analysis_source_status??'repository'}; native identity {show(point.source_native_identity)}; source CalcJob {show(point.source_calcjob_uuid)}.</p>}
 <button type="button" disabled={!point?.source_commit_sha256||busy||(provider==='nfs_registered'&&(brief.storage_binding_status==='binding_unavailable'||point.analysis_source_status!=='registered'))} onClick={()=>void load('manifest')}>Describe saved views</button></>}
 {manifest&&<><label>Saved semantic view<select value={viewId} onChange={e=>{invalidate();setViewId(e.target.value);setFields({});}}><option value="">Select a view</option>{manifest.views.map(v=><option key={v.view_id} value={v.view_id} disabled={v.availability!=='available'}>{v.view_id} · {v.units??''} · {v.availability}{v.reason?': '+v.reason:''}</option>)}</select></label>
 <details><summary>Saved source and capabilities</summary><pre>{JSON.stringify({files:manifest.files,integrity:manifest.integrity,views:manifest.views},null,2)}</pre></details></>}
 {view?.availability==='available'&&<><p>Native shape {view.shape?.join(' × ')}; {view.basis??'no basis'}; {view.units}. Explicit component: {view.view_id}. Matrix/state display bounds are enforced by Results.</p>
 {Array.from({length:view.allowed_selection!.axis_count},(_,i)=><fieldset key={`${viewId}:${i}`}><legend>{view.axes?.[i]} native axis (0…{(view.shape?.[i]??1)-1})</legend>
 <select aria-label={`Axis ${i} selection mode`} value={fields[`${i}:mode`]??'range'} onChange={e=>{invalidate();setFields(f=>({...f,[`${i}:mode`]:e.target.value}));}}><option value="range">Explicit range</option><option value="index">Explicit index</option></select>
 {(fields[`${i}:mode`]==='index'?['index']:['start','stop','step']).map(key=><label key={key}>{key}{key==='stop'?' (exclusive)':''}<input inputMode="numeric" value={fields[`${i}:${key}`]??''} onChange={e=>{invalidate();setFields(f=>({...f,[`${i}:${key}`]:e.target.value}));}}/></label>)}</fieldset>)}
 <button type="button" disabled={busy} onClick={()=>void load('slice')}>Read exact native selection</button></>}
 {slice&&<SavedPlot key={`${slice.source_commit_sha256}/${slice.view_id}/${JSON.stringify(slice.returned_shape)}/${JSON.stringify(fields)}`} slice={slice}/>}</section>;
}
