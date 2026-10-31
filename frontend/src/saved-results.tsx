import {useEffect,useRef,useState} from 'react';
import {SavedDiagnostics} from './saved-diagnostics.tsx';
import {api,errorMessage} from './api';
import type {Run} from './api';
import {cellEdges,displayScale,readManifest,readSlice,nativePin,SavedSession,selectionOptions,pointChoice,pointRequest,readPointSource,pointContext,catalogChoice,catalogRequest,readSources,readBrief,declaredText,ieeeCell,historySelection,historyReduction,historyAssessment,historyRunUUID,readHistoryDerive,readHistoryComparison,HistorySession} from './saved-results-data';
import type {Masks,Numbers,SavedAxis,SavedSlice,SavedView,PointSource,NativeEnvelope,SavedManifest,SourcesPage,BriefPage,BriefPoint,HistoryReduction,HistoryAssessment,HistoryDerived,HistoryComparison,HistoryResult,CatalogPoint} from './saved-results-data';

const show=(v:unknown,precision?:number)=>v==null?'unknown':typeof v==='object'?JSON.stringify(v):typeof v==='number'?(Object.is(v,-0)?'-0':precision?v.toPrecision(precision):String(v)):String(v);
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
  <text x="52" y="275">{show(xmin,5)}</text><text x="565" y="275">{show(xmax,5)}</text>
  <text x="4" y="40">{show(ymax,4)}</text><text x="4" y="250">{show(ymin,4)}</text>
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
  const integer=(key:string)=>{const raw=fields[`${i}:${key}`]??'';if(!/^(0|[1-9][0-9]*)$/.test(raw))throw new Error('Укажите точные неотрицательные индексы или диапазоны');const n=Number(raw);if(!Number.isSafeInteger(n))throw new Error('Индекс превышает точный числовой предел');return n;};
  if(fields[`${i}:mode`]==='index')return integer('index');
  const start=integer('start'),stop=integer('stop'),step=integer('step');if(stop<=start||step<1)throw new Error('Конец должен быть больше начала, шаг — положительным');return {start,stop,step};
 });
}
export function PointSourceSummary({source}:{source:PointSource}){return <><p>Получены метаданные исходного владельца. Доступ к файлам пока не проверен; научная приёмка не установлена.</p>
 {source.source.kind==='absent'?<p>Источник отсутствует: {source.source.reason}</p>:<p>{source.source.kind==='checkpoint'?'Сохранён checkpoint; финальные карты не объявлены.':'Объявлено финальное состояние; доступность чтения проверится при запросе.'} {source.source.relation==='retained_source'&&'Сохранённый источник другой попытки.'} {source.source.reason}</p>}
 <details><summary>Исходные идентификаторы и область проверки</summary><pre>{JSON.stringify(source,null,2)}</pre></details></>;}
export function SavedResultsPanel({token,run}:{token:string;run:Run}){
 const options=selectionOptions(run),session=useRef(new SavedSession()),identity=useRef('');
 const [choice,setChoice]=useState(''),[pointId,setPointId]=useState(''),[generation,setGeneration]=useState(0);
 const selected=pointChoice(token,run,choice,pointId),{selection,point,key}=selected,contextKey=JSON.stringify([key,generation]);
 if(identity.current!==key){session.current.invalidate();identity.current=key;}
 const [metadata,setMetadata]=useState<{key:string;value:PointSource}|null>(null),[stored,setStored]=useState<{key:string;value:NativeEnvelope<SavedManifest>}|null>(null),[slice,setSlice]=useState<{key:string;value:SavedSlice}|null>(null);
 const [viewId,setViewId]=useState(''),[fields,setFields]=useState<Record<string,string>>({}),[error,setError]=useState<{key:string;text:string}|null>(null),[busy,setBusy]=useState(''),[diagnostics,setDiagnostics]=useState(false);
 const source=metadata?.key===contextKey?metadata.value:null,context=source?pointContext(source):null;
 const manifest=source&&stored?.key===contextKey?stored.value:null,shown=source&&slice?.key===contextKey?slice.value:null,view=manifest?.view.views[viewId];
 const points=(run.plan_points??[]).filter(p=>p.execution_id===selection?.execution_id);
 function invalidate(){session.current.invalidate();setSlice(null);setError(null);setBusy('');}
 function reset(){invalidate();setMetadata(null);setStored(null);setViewId('');setFields({});setDiagnostics(false);}
 useEffect(()=>{reset();return()=>session.current.invalidate();},[key]);
 async function refresh(){
  if(!selection||!point)return;
  reset();const g=session.current.begin(),nextKey=JSON.stringify([key,g]);setGeneration(g);setBusy(nextKey);
  const exact=selection,exactPoint=point.id;
  await session.current.receive(g,async()=>{
   const query=new URLSearchParams(Object.entries(pointRequest(exact,exactPoint)).map(([k,v])=>[k,String(v)]));
   return readPointSource(await api(token,`/runs/${encodeURIComponent(run.uuid)}/saved-results/point-source?${query}`),exact,exactPoint);
  },value=>{setMetadata({key:nextKey,value:value as PointSource});setBusy('');},e=>{setError({key:nextKey,text:errorMessage(e)});setBusy('');});
 }
 async function load(operation:'manifest'|'slice'){
  if(!context)return;const exact=context,g=session.current.begin();setBusy(contextKey);setError(null);
  await session.current.receive(g,async()=>{
   const pin=nativePin(exact),base=`/runs/${encodeURIComponent(run.uuid)}/saved-results/`;
   if(operation==='manifest'){const query=new URLSearchParams(Object.entries(pin).map(([k,v])=>[k,String(v)]));return readManifest(await api(token,base+'manifest?'+query),exact);}
   if(!manifest||!view)throw new Error('Выберите доступное сохранённое представление');
   return readSlice(await api(token,base+'slice',{...pin,source_state:exact.state_ref,view_id:viewId,selection:nativeSelection(view,fields)}),exact,viewId);
  },value=>{if(operation==='manifest'){setStored({key:contextKey,value:value as NativeEnvelope<SavedManifest>});setSlice(null);setViewId('');setFields({});}else setSlice({key:contextKey,value:(value as NativeEnvelope).view as unknown as SavedSlice});setBusy('');},e=>{setError({key:contextKey,text:errorMessage(e)});setBusy('');});
 }
 return <section className="saved-results"><h3>Сохранённые результаты точки</h3>
 <p>Выберите исполнение, транспортную попытку, CalcJob и точку исходного плана. Затем явно обновите сведения источника. Номер native-попытки хранится отдельно; чтение не запускает расчёт или derive.</p>
 <p>Каталог/J(V) (R1) читается явно по конечным страницам. Текущая история (R3), retrieved (R4) и performance (R5) остаются отдельными контрактами.</p>
 <label>Исполнение / попытка / CalcJob<select value={choice} onChange={e=>{reset();setChoice(e.target.value);setPointId('');}}><option value="">Выберите явно</option>{options.map(v=><option key={v.calcjob_uuid} value={v.calcjob_uuid}>{v.execution_id} · попытка {v.attempt} · {v.calcjob_uuid}</option>)}</select></label>
 <SavedCatalogPanel token={token} run={run} job={choice}/>
 <label>Точка исходного плана<select value={pointId} onChange={e=>{reset();setPointId(e.target.value);}}><option value="">Выберите точку</option>{points.map(p=><option key={p.id} value={p.id}>{p.id} · {show(p.temperature_K)} K · {show(p.voltage_per_period_V)} V</option>)}</select></label>
 {!points.length&&<p>Список точек исходного плана для этого исполнения недоступен.</p>}
 <button type="button" disabled={!selection||!point||busy===contextKey} onClick={()=>void refresh()}>Обновить сведения выбранной точки</button>
 {busy===contextKey&&<p role="status">Чтение выбранных сохранённых сведений…</p>}{error?.key===contextKey&&<p role="alert" className="error">{error.text}</p>}
 {source&&<PointSourceSummary source={source}/>}
 {context&&<><button type="button" disabled={source?.source.kind!=='final'||busy===contextKey} onClick={()=>void load('manifest')}>Прочитать описание сохранённых представлений</button>
 <button type="button" disabled={busy===contextKey} onClick={()=>setDiagnostics(true)}>Открыть сохранённую диагностику и историю</button></>}
 {diagnostics&&context&&<SavedDiagnostics key={contextKey} token={token} context={context}/>}
 {manifest&&<><p>Текущее чтение не перепроверяет native-контексты и научную приёмку. Историческая binding проверяет выбранные метаданные. Наблюдение RO-доступа относится только к этому запросу.</p><details><summary>Исходное состояние, provenance и наблюдение доступа</summary><pre>{JSON.stringify(manifest,null,2)}</pre></details>
 <label>Сохранённое представление<select value={viewId} onChange={e=>{invalidate();setViewId(e.target.value);setFields({});}}><option value="">Выберите представление</option>{Object.values(manifest.view.views).map(v=><option key={v.view_id} value={v.view_id} disabled={v.availability!=='available'}>{v.view_id} · {v.units} · {v.availability==='available'?'доступно':'недоступно'} {v.issue?JSON.stringify(v.issue):''}</option>)}</select></label></>}
 {view?.availability==='available'&&<><p>Исходный размер {view.shape?.join(' × ')}; базис {view.basis??'не указан'}; {view.units}. Задайте точные индексы или диапазоны.</p>
 {Array.from({length:view.allowed_selection!.axis_count},(_,i)=><fieldset key={`${viewId}:${i}`}><legend>{view.axes?.[i]}: исходная ось</legend>
 <select aria-label={`Ось ${i}: режим выборки`} value={fields[`${i}:mode`]??'range'} onChange={e=>{invalidate();setFields(f=>({...f,[`${i}:mode`]:e.target.value}));}}><option value="range">Диапазон</option><option value="index">Индекс</option></select>
 {(fields[`${i}:mode`]==='index'?['index']:['start','stop','step']).map(k=><label key={k}>{({index:'Индекс',start:'Начало',stop:'Конец (не включается)',step:'Шаг'} as Record<string,string>)[k]}<input value={fields[`${i}:${k}`]??''} onChange={e=>{invalidate();setFields(f=>({...f,[`${i}:${k}`]:e.target.value}));}}/></label>)}</fieldset>)}
 <button type="button" disabled={busy===contextKey} onClick={()=>void load('slice')}>Прочитать точную выборку</button></>}
 {shown&&<SavedPlot slice={shown}/>}</section>;
}

export function SavedBrief({page}:{page:BriefPage}){
 const plotted=page.points.filter(p=>p.view).map(p=>({id:p.point_id,x:ieeeCell(p.view!.request.coordinate_checks.voltage.actual),j:ieeeCell(p.view!.scalar_observables.current_density_A_per_m2)})).filter(p=>p.x.value!==null&&p.j.value!==null);
 return <><p>J — сохранённая знаковая плотность тока в A/m²; V — actual voltage per period из сохранённого request. Точки показаны без соединяющих линий. Сопоставимость физических постановок не установлена.</p>
 <Lines markersOnly paths={plotted.map(p=>[[p.x.value!,p.j.value!]])} labels={plotted.map(p=>p.id)} xlabel="V per period (V)" ylabel="J (A/m²)"/>
 <p>Process, сходимость, physical gates, discretization и научная приёмка оцениваются раздельно. Неизмеренное остаётся not_measured.</p></>;
}

export function SavedCatalogPanel({token,run,job}:{token:string;run:Run;job:string}){
 const {selection,key}=catalogChoice(token,run,job),session=useRef(new SavedSession()),identity=useRef('');
 if(identity.current!==key){session.current.invalidate();identity.current=key;}
 const [metadata,setMetadata]=useState<{key:string;value:SourcesPage}|null>(null),[brief,setBrief]=useState<{key:string;value:BriefPage}|null>(null);
 const [busy,setBusy]=useState<{key:string;operation:string}|null>(null),[error,setError]=useState<{key:string;text:string}|null>(null);
 const page=metadata?.key===key?metadata.value:null,view=page&&brief?.key===key&&brief.value.pagination.page_ref===page.pagination.page_ref?brief.value:null;
 const pending=busy?.key===key;
 useEffect(()=>{session.current.invalidate();setMetadata(null);setBrief(null);setError(null);setBusy(null);return()=>session.current.invalidate();},[key]);
 async function sources(cursor?:string){
  if(!selection)return;
  const exact=selection,g=session.current.begin();setMetadata(null);setBrief(null);setError(null);setBusy({key,operation:'catalog'});
  await session.current.receive(g,async()=>{
   const query=new URLSearchParams(Object.entries(catalogRequest(exact,cursor?{cursor}:{limit:8})).map(([k,v])=>[k,String(v)]));
   return readSources(await api(token,`/runs/${encodeURIComponent(exact.run_uuid)}/saved-results/sources?${query}`),exact,cursor);
  },value=>{setMetadata({key,value:value as SourcesPage});setBusy(null);},e=>{setError({key,text:errorMessage(e)});setBusy(null);});
 }
 async function readPage(){
  if(!page)return;
  const exact=page,g=session.current.begin();setBrief(null);setError(null);setBusy({key,operation:'brief'});
  await session.current.receive(g,async()=>{
   const query=new URLSearchParams(Object.entries(catalogRequest(exact.provider_selection,{page_ref:exact.pagination.page_ref})).map(([k,v])=>[k,String(v)]));
   return readBrief(await api(token,`/runs/${encodeURIComponent(exact.provider_selection.run_uuid)}/saved-results/brief?${query}`),exact);
  },value=>{setBrief({key,value:value as BriefPage});setBusy(null);},e=>{setError({key,text:errorMessage(e)});setBusy(null);});
 }
 const rows=view?.points??page?.points??[];
 return <section aria-label="Каталог сохранённых точек"><h4>Каталог и J(V) выбранного исполнения</h4>
 <p>Исходный frozen plan включает необсчитанные точки. Declared координаты не являются used snapshot. Чтение страницы не запускает расчёт.</p>
 <button type="button" disabled={!selection||pending} onClick={()=>void sources()}>Прочитать каталог</button>
 {pending&&<p role="status">Чтение {busy?.operation==='brief'?'J(V) текущей страницы':'каталога'}…</p>}
 {error?.key===key&&<p role="alert" className="error">{error.text}</p>}
 {page&&<><p>Offset {page.pagination.offset}; на странице {page.points.length} из {page.pagination.total} объявленных точек. Метаданные исходного владельца; доступ к файлам ещё не проверен.</p>
 <button type="button" disabled={pending||page.pagination.previous_cursor===null} onClick={()=>void sources(page.pagination.previous_cursor!)}>Предыдущая страница</button>
 <button type="button" disabled={pending||page.pagination.next_cursor===null} onClick={()=>void sources(page.pagination.next_cursor!)}>Следующая страница</button>
 <button type="button" disabled={pending} onClick={()=>void readPage()}>Прочитать J(V) этой страницы</button>
 <div className="saved-table"><table><thead><tr><th>Точка / branch / order</th><th>Declared T (K), V per period (V)</th><th>Источник / статус</th><th>Actual V (V)</th><th>Signed J (A/m²)</th></tr></thead><tbody>{rows.map(row=>{
  const final=(row as BriefPoint).view,x=final?ieeeCell(final.request.coordinate_checks.voltage.actual):null,j=final?ieeeCell(final.scalar_observables.current_density_A_per_m2):null;
  const issue=final?.issues.find(i=>i.target==='current_density_A_per_m2');
  return <tr key={row.point_id}><td>{row.point_id} · {row.declared_coordinates.branch} · {row.declared_coordinates.order}</td><td>{declaredText(row.declared_coordinates.temperature_K)} K; {declaredText(row.declared_coordinates.voltage_per_period_V)} V</td><td>{row.source?.kind??row.missing_reason}; {row.source?.reason}; {JSON.stringify(row.recorded_statuses)}</td><td>{x?.text??'not_read'}</td><td>{j?.text??'not_read'} {issue?.reason?String(issue.reason):''}</td></tr>;
 })}</tbody></table></div>
 <details><summary>Исходные pins, full StateRef и статусы страницы</summary><pre>{JSON.stringify(page,null,2)}</pre></details></>}
 {view&&<><SavedBrief page={view}/><p>Наблюдение RO относится только к этому запросу. Original binding integrity, public selected-byte integrity и scientific acceptance различаются.</p><details><summary>Raw IEEE tags, actual request, original/current/restoration evidence и I/O epochs</summary><pre>{JSON.stringify(view,null,2)}</pre></details></>}
 <SavedHistoryPanel token={token} run={run} page={page}/>
 </section>;
}

interface HistorySource {page:SourcesPage;row:CatalogPoint}
const emptyReduction=():HistoryReduction=>({stage:'',field:'',first_record:'',last_record:''});
export function HistoryResultView({result}:{result:HistoryResult}){return <><p>Поле {result.request.stage}/{result.request.field}; записи {result.request.first_record}–{result.request.last_record}; units: {result.units===null?'null':result.units}.</p><p>observations {result.observations}; updates {result.updates}; finite {result.finite_count}; nonfinite {result.nonfinite_count}; missing {result.missing_count}.</p><dl>{(['minimum','maximum'] as const).map(k=><div key={k}><dt>{k}</dt><dd>{result[k]===null?'null (конечное значение отсутствует)':`${result[k]!.value.type} IEEE bits ${result[k]!.value.bits}`}<pre>{JSON.stringify(result[k]?.locator??null,null,2)}</pre></dd></div>)}</dl></>;}
function ReductionFields({side,value,edit}:{side:string;value:HistoryReduction;edit:(v:HistoryReduction)=>void}){return <fieldset><legend>{side}: явная редукция сохранённой истории</legend><label>Stage<select value={value.stage} onChange={e=>edit({...value,stage:e.target.value})}><option value="">Укажите stage</option><option value="scba">scba</option><option value="outer">outer</option></select></label>{(['field','first_record','last_record'] as const).map(k=><label key={k}>{k}<input value={value[k]} onChange={e=>edit({...value,[k]:e.target.value})}/></label>)}</fieldset>;}
function HistoryRows({page,label,choose}:{page:SourcesPage;label:string;choose:(source:HistorySource)=>void}){return <div className="saved-table"><table><thead><tr><th>Точка / native attempt</th><th>Явное закрепление источника</th></tr></thead><tbody>{page.points.map(row=><tr key={row.point_id}><td>{row.point_id} · {row.point_attempt??'not_recorded'} · {row.source?.kind??'not_recorded'}</td><td><button type="button" disabled={!row.source_state||!row.source_invocation||row.source?.kind==='absent'} onClick={()=>choose(structuredClone({page,row}))}>{label}: {row.point_id}</button></td></tr>)}</tbody></table></div>;}
/** A second run is fetched only by this explicit action; pages never auto-walk. */
export function RightHistoryPicker({token,choose,invalidate}:{token:string;choose:(source:HistorySource)=>void;invalidate:()=>void}){
 const session=useRef(new SavedSession()),key=useRef(token),[runId,setRunId]=useState(''),[run,setRun]=useState<Run|null>(null),[job,setJob]=useState(''),[page,setPage]=useState<SourcesPage|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
 if(key.current!==token){session.current.invalidate();key.current=token;}
 useEffect(()=>()=>session.current.invalidate(),[]);
 function edit(){session.current.invalidate();invalidate();setPage(null);setError('');setBusy(false);}
 async function getRun(){edit();setRun(null);setJob('');const g=session.current.begin();setBusy(true);await session.current.receive(g,async()=>{const id=historyRunUUID(runId);const value=await api(token,`/runs/${encodeURIComponent(id)}`) as Run;if(value.uuid!==id)throw new Error('Другой run в ответе');return value;},v=>{setRun(v as Run);setBusy(false);},e=>{setError(errorMessage(e));setBusy(false);});}
 async function sources(cursor?:string){if(!run)return;const selected=selectionOptions(run).find(v=>v.calcjob_uuid===job);if(!selected)return;edit();const g=session.current.begin();setBusy(true);await session.current.receive(g,async()=>{const query=new URLSearchParams(Object.entries(catalogRequest(selected,cursor?{cursor}:{limit:8})).map(([k,v])=>[k,String(v)]));return readSources(await api(token,`/runs/${encodeURIComponent(selected.run_uuid)}/saved-results/sources?${query}`),selected,cursor);},v=>{setPage(v as SourcesPage);setBusy(false);},e=>{setError(errorMessage(e));setBusy(false);});}
 return <fieldset><legend>Правый источник: отдельный явный run</legend><label>Run UUID<input value={runId} onChange={e=>{edit();setRun(null);setJob('');setRunId(e.target.value);}}/></label><button type="button" disabled={busy||!runId} onClick={()=>void getRun()}>Получить исполнения правого run</button>{run&&<><label>Правое исполнение / попытка / CalcJob<select value={job} onChange={e=>{edit();setJob(e.target.value);}}><option value="">Выберите явно</option>{selectionOptions(run).map(s=><option key={s.calcjob_uuid} value={s.calcjob_uuid}>{s.execution_id} · {s.attempt} · {s.calcjob_uuid}</option>)}</select></label><button type="button" disabled={busy||!job} onClick={()=>void sources()}>Прочитать правый каталог</button></>}{busy&&<p role="status">Чтение правого metadata…</p>}{error&&<p role="alert" className="error">{error}</p>}{page&&<><p>Run {page.provider_selection.run_uuid}; offset {page.pagination.offset}; всего {page.pagination.total}. Прочитана одна конечная страница.</p><button type="button" disabled={busy||!page.pagination.previous_cursor} onClick={()=>void sources(page.pagination.previous_cursor!)}>Предыдущая правая страница</button><button type="button" disabled={busy||!page.pagination.next_cursor} onClick={()=>void sources(page.pagination.next_cursor!)}>Следующая правая страница</button><HistoryRows page={page} label="Закрепить справа" choose={choose}/></>}</fieldset>;
}
export function SavedHistoryPanel({token,run,page}:{token:string;run:Run;page:SourcesPage|null}){
 const session=useRef(new HistorySession()),scope=useRef('');
 const context=JSON.stringify([token,run.uuid,run.plan_fingerprint??null,page?.provider_selection??null,page?.retained??null,page?.pagination.page_ref??null]);
 if(scope.current!==context){session.current.invalidate();scope.current=context;}
 const [left,setLeft]=useState<HistorySource|null>(null),[right,setRight]=useState<HistorySource|null>(null),[leftReduction,setLeftReduction]=useState(emptyReduction),[rightReduction,setRightReduction]=useState(emptyReduction);
 const [assessment,setAssessment]=useState<HistoryAssessment>({model:'unknown',discretization:'unknown',protocol:'unknown',source:'unknown',evidence:[],reason:''});
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[result,setResult]=useState<{context:string;value:HistoryDerived|HistoryComparison}|null>(null);
 function invalidate(){session.current.invalidate();setBusy(false);setError('');setResult(null);}
 useEffect(()=>{invalidate();return()=>session.current.invalidate();},[context]);
 useEffect(()=>{setLeft(null);setRight(null);setLeftReduction(emptyReduction());setRightReduction(emptyReduction());},[token,run.uuid]);
 function choose(side:'left'|'right',source:HistorySource){invalidate();(side==='left'?setLeft:setRight)(source);}
 async function act(compare:boolean){
  if(!left)return;const g=session.current.beginAction();if(g===null)return;setBusy(true);setError('');setResult(null);
  await session.current.receiveAction(g,async()=>{
   const a=historySelection(left.page,left.row,historyReduction(leftReduction));let body:unknown,value:HistoryDerived|HistoryComparison;
   if(compare){if(!right)throw new Error('Закрепите правый источник явно');const b=historySelection(right.page,right.row,historyReduction(rightReduction)),assessmentCopy=historyAssessment(assessment);body={left:a,right:b,comparability:assessmentCopy};value=readHistoryComparison(await api(token,`/runs/${encodeURIComponent(a.run_uuid)}/saved-results/compare-history`,body),a,b,assessmentCopy);}
   else {const {run_uuid,...request}=a;body=request;value=readHistoryDerive(await api(token,`/runs/${encodeURIComponent(run_uuid)}/saved-results/derive-history`,body),a);}
   return value;
  },v=>{setResult({context,value:v as HistoryDerived|HistoryComparison});setBusy(false);},e=>{setError(errorMessage(e));setBusy(false);});
 }
 const shown=result?.context===context?result.value:null;
 return <section aria-label="Явная редукция и сравнение сохранённой истории"><h4>Сохранённая история: derive / compare</h4><p>Действия запускаются только кнопкой POST. Results читает весь frozen catalog и prefix истории до диапазона, включая возможный batch overshoot. Узкий диапазон не обещает малого I/O. Solver не запускается.</p><p>Выберите левую точку текущей страницы и правый источник явно. Поля и границы задаются без научных defaults. Закреплённые pins сохраняются до явной замены или смены run/token.</p>{page?<HistoryRows page={page} label="Закрепить слева" choose={s=>choose('left',s)}/>:<p>Сначала явно прочитайте каталог выбранного исполнения.</p>}
 <RightHistoryPicker key={JSON.stringify([token,run.uuid])} token={token} choose={s=>choose('right',s)} invalidate={invalidate}/>
 {(['left','right'] as const).map(side=>{const source=side==='left'?left:right;return <div key={side}><p>{side}: {source?`${source.page.provider_selection.run_uuid} · ${source.page.provider_selection.execution_id} · transport attempt ${source.page.provider_selection.attempt} · ${source.row.point_id} · native attempt ${source.row.source_state?.identity.attempt}`:'не закреплён'}</p>{source&&<><button type="button" onClick={()=>{invalidate();(side==='left'?setLeft:setRight)(null);}}>Снять {side}</button><details><summary>Полные immutable selectors, StateRef, invocation и четыре pins ({side})</summary><pre>{JSON.stringify({provider_selection:source.page.provider_selection,retained:source.page.retained,row:source.row},null,2)}</pre></details></>}<ReductionFields side={side} value={side==='left'?leftReduction:rightReduction} edit={v=>{invalidate();(side==='left'?setLeftReduction:setRightReduction)(v);}}/></div>;})}
 <fieldset><legend>Сопоставимость — явное заявление автора</legend>{(['model','discretization','protocol','source'] as const).map(k=><label key={k}>{k}<select value={assessment[k]} onChange={e=>{invalidate();setAssessment({...assessment,[k]:e.target.value});}}><option value="unknown">unknown</option><option value="different">different</option><option value="comparable">comparable</option></select></label>)}<label>Evidence locators (по одному в строке, до 16)<textarea value={assessment.evidence.join('\n')} onChange={e=>{invalidate();setAssessment({...assessment,evidence:e.target.value?e.target.value.split('\n'):[]});}}/></label><label>Reason<textarea value={assessment.reason} onChange={e=>{invalidate();setAssessment({...assessment,reason:e.target.value});}}/></label></fieldset>
 <button type="button" disabled={busy||!left} onClick={()=>void act(false)}>Вычислить редукцию левой истории</button><button type="button" disabled={busy||!left||!right} onClick={()=>void act(true)}>Сравнить две закреплённые истории</button>{busy&&<p role="status">Явное чтение и редукция истории…</p>}{error&&<p role="alert" className="error">{error}</p>}
 {shown&&<><p>scientific_acceptance: not_assessed. У сравнения comparability author_declared; unknown/different не дают differences. Совпадение hashes, process или convergence не устанавливает сопоставимость.</p>{shown.operation==='derive-history'?<HistoryResultView result={(shown as HistoryDerived).view.result}/>:<><HistoryResultView result={(shown as HistoryComparison).view.left}/><HistoryResultView result={(shown as HistoryComparison).view.right}/><p>Owning differences (исходные IEEE tags): {JSON.stringify((shown as HistoryComparison).view.differences)}</p></>}<details><summary>Полный Results ответ, cutoff, locators и I/O/copy credits</summary><pre>{JSON.stringify(shown,null,2)}</pre></details></>}
 </section>;
}
