import { useEffect, useRef, useState } from 'react';
import { errorMessage, fragmentIdPattern, getModelHelp, type DocumentationAvailability,
  type Run, type UnknownModelHelp } from './api.ts';
import { checkedBundle, checkedFragment, checkedJson, docHref, referenceIdentity, RequestEpoch,
  type Descriptor, type ReferenceFragment } from './model-help-data.ts';

export function RunModelHelp({token, run}: {token: string; run: Run}) {
  return <RunContextPanel key={`${token}\0${run.uuid}`} token={token} run={run} />;
}
function RunContextPanel({token,run}: {token: string; run: Run}) {
  const [open,setOpen]=useState(false);
  const [pointKey,setPointKey]=useState(''); const [childUuid,setChildUuid]=useState('');
  const [fragmentId,setFragmentId]=useState('');
  const [view,setView]=useState<UnknownModelHelp|null>(null);
  const [loading,setLoading]=useState(false); const [error,setError]=useState('');
  const epoch=useRef(new RequestEpoch()); const opener=useRef<HTMLButtonElement>(null);
  useEffect(()=>()=>epoch.current.invalidate(),[]);
  function reset() {epoch.current.invalidate();setView(null);setLoading(false);setError('');}
  const point=run.plan_points?.find(p=>`${p.execution_id}:${p.id}`===pointKey);
  const children=(run.children??[]).filter(c=>c.execution_id===point?.execution_id);
  const child=children.find(c=>c.uuid===childUuid);
  const ready=Boolean(point && child && Number.isSafeInteger(child.attempt) && child.attempt>0 && fragmentIdPattern.test(fragmentId));
  const identity=point && child ? `${point.execution_id}\0${point.id}\0${child.uuid}\0${child.attempt}\0${fragmentId}` : '';
  const identityRef=useRef(identity); identityRef.current=identity;
  // Polling get_run may remove a selected child. It must not revive an old view.
  useEffect(()=>{reset();},[identity]);
  async function read() {
    if(!ready || !point || !child) return;
    reset();setLoading(true); const ticket=epoch.current.next(); const selectedIdentity=identity;
    const active=()=>epoch.current.current(ticket) && identityRef.current===selectedIdentity;
    try {
      const value=await getModelHelp(token,run.uuid,fragmentId,{execution_id:point.execution_id,
        point_id:point.id,attempt:child.attempt,calcjob_uuid:child.uuid});
      if(active()) setView(value);
    } catch(err) {if(active()) setError(errorMessage(err));}
    finally {if(active()) setLoading(false);}
  }
  function close(){reset();setOpen(false);opener.current?.focus();}
  const visibleView=view && view.context.execution_id===point?.execution_id && view.context.point_id===point?.id &&
    view.context.calcjob_uuid===child?.uuid && view.context.attempt===child?.attempt ? view : null;
  return <section className="model-help" aria-label="Модель и источники">
    <h4>Модель и источники</h4>
    <button type="button" ref={opener} aria-expanded={open} onClick={()=>{if(open) close();else setOpen(true);}}>Контекст выбранного run</button>
    {open && <div className="help-content">
      <p>Выберите точные point, child и идентификатор фрагмента. Чтение выполняется по кнопке.</p>
      <label>Point и execution<select value={pointKey} onChange={e=>{reset();setPointKey(e.target.value);setChildUuid('');}}>
        <option value="">Выберите point</option>
        {(run.plan_points??[]).map(p=><option key={`${p.execution_id}:${p.id}`} value={`${p.execution_id}:${p.id}`}>
          {p.id} · {p.execution_id} · {p.temperature_K} K · {p.voltage_per_period_V} V
        </option>)}
      </select></label>
      {!run.plan_points?.length && <p className="muted">Точный список point недоступен.</p>}
      <label>Child attempt и UUID<select value={childUuid} onChange={e=>{reset();setChildUuid(e.target.value);}}>
        <option value="">Выберите child</option>
        {children.map(c=><option key={c.uuid} value={c.uuid}>Attempt {c.attempt} · {c.uuid}</option>)}
      </select></label>
      <label>Fragment ID<input value={fragmentId} onChange={e=>{reset();setFragmentId(e.target.value);}} maxLength={128} placeholder="Идентификатор из опубликованной справки" /></label>
      <div className="help-actions"><button type="button" disabled={!ready || loading} onClick={()=>void read()}>Прочитать контекст</button>
        <button type="button" onClick={close}>Закрыть контекст</button></div>
      {loading && <p role="status">Чтение контекста…</p>}
      {error && <p role="alert">{error}</p>}
      {visibleView && <UnknownContext view={visibleView} />}
    </div>}
  </section>;
}
export function UnknownContext({view}: {view: UnknownModelHelp}) {
  return <div className="help-unknown">
    <p>Привязка источников недоступна</p><p>Данные declared/used недоступны</p>
    <p className="muted">HTTP 200 подтверждает чтение metadata контекста. Научная приёмка оценивается отдельно.</p>
    <dl>{Object.entries(view.context).map(([key,value])=><div key={key}><dt>{key}</dt><dd><code>{value===null?'Недоступно':String(value)}</code></dd></div>)}
      <dt>Recorded release</dt><dd><code>{view.binding.release_id??'Недоступно'}</code></dd></dl>
  </div>;
}

export function PublishedReference({documentation}: {documentation?: DocumentationAvailability}) {
  const id=referenceIdentity(documentation);
  return <ReferencePanel key={id?`${id.revision}:${id.sha}`:'unavailable'} documentation={documentation} />;
}
function ReferencePanel({documentation}: {documentation?: DocumentationAvailability}) {
  const id=referenceIdentity(documentation);
  const [descriptors,setDescriptors]=useState<Descriptor[]|null>(null);
  const [fragment,setFragment]=useState<ReferenceFragment|null>(null);
  const [selected,setSelected]=useState(''); const [loading,setLoading]=useState(false);
  const [error,setError]=useState(''); const epoch=useRef(new RequestEpoch());
  const revision=id?.revision, url=id?.url, sha=id?.sha;
  useEffect(()=>{
    const ticket=epoch.current.next(); setDescriptors(null);setFragment(null);setSelected('');setError('');
    if(!revision || !url || !sha) return ()=>epoch.current.invalidate();
    setLoading(true);
    void checkedJson(`${url}bundle.json`,2*1024*1024,sha)
      .then(value=>{const list=checkedBundle(value,revision);if(epoch.current.current(ticket))setDescriptors(list);})
      .catch(err=>{if(epoch.current.current(ticket))setError(errorMessage(err));})
      .finally(()=>{if(epoch.current.current(ticket))setLoading(false);});
    return ()=>epoch.current.invalidate();
  },[revision,url,sha]);
  async function select(value: string) {
    const ticket=epoch.current.next();setSelected(value);setFragment(null);setError('');setLoading(Boolean(value));
    const d=descriptors?.find(d=>d.id===value);
    if(!d || !id){setLoading(false);return;}
    try {
      const f=checkedFragment(await checkedJson(`${id.url}${d.file}`,32768,d.sha256),id.revision,d.id);
      if(epoch.current.current(ticket))setFragment(f);
    }catch(err){if(epoch.current.current(ticket))setError(errorMessage(err));}
    finally{if(epoch.current.current(ticket))setLoading(false);}
  }
  return <aside className="model-help panel" aria-label="Справка опубликованной документации">
    <h3>Справка опубликованной документации {id?.revision??'недоступна'}</h3>
    <p className="muted">Статический опубликованный источник. Использование или declared значения выбранного run этой справкой не подтверждаются.</p>
    {id && <nav className="documentation" aria-label="Маршруты опубликованной документации">
      <a href={id.url}>Главная</a>
      {(['researcher','developer','operator'] as const).map((key,index)=>{
        const href=documentation?.journeys[key];
        return href && href.startsWith(id.url) && !/[\\%?\s]/.test(href) && !href.includes('..') ?
          <a key={key} href={href}>{['Исследователю','Разработчику','Оператору'][index]}</a>:null;
      })}
      <a href={`${id.url}runner/api/public.html#reader-api-routes`}>Reader API</a>
    </nav>}
    {!id && <p>Опубликованная справка недоступна.</p>}
    {loading && <p role="status">Чтение опубликованной справки…</p>}
    {error && <p role="alert">Опубликованная справка недоступна: {error}</p>}
    {descriptors?.length===0 && <p>Контекстных фрагментов нет</p>}
    {Boolean(descriptors?.length) && <label>Статический фрагмент<select value={selected} onChange={e=>void select(e.target.value)}>
      <option value="">Выберите фрагмент</option>{descriptors?.map(d=><option key={d.id} value={d.id}>{d.id}</option>)}
    </select></label>}
    {fragment && id && <StaticFragment fragment={fragment} revision={id.revision} />}
  </aside>;
}
export function StaticFragment({fragment,revision}: {fragment:ReferenceFragment;revision:string}) {
  return <article className="help-reference"><h4>{fragment.title}</h4><p>{fragment.summary}</p>
    <p>Fragment ID: <code>{fragment.id}</code> · scope: {fragment.scope}</p>
    <a href={docHref(revision,fragment.full)}>Полный раздел</a>
    {fragment.related.map((d,i)=><p key={i}><a href={docHref(revision,d)}>Связанный раздел {i+1}</a></p>)}
    {fragment.assumptions.length>0 && <><h5>Предположения</h5><ul>{fragment.assumptions.map((s,i)=><li key={i}>{s}</li>)}</ul></>}
    {fragment.limitations.length>0 && <><h5>Ограничения</h5><ul>{fragment.limitations.map((s,i)=><li key={i}>{s}</li>)}</ul></>}
  </article>;
}
