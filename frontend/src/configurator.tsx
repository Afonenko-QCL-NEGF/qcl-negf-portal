import {useEffect, useRef, useState} from 'react';
import {api, errorMessage} from './api.ts';
import {PreviewSession, makeNumericalVariant, makeRequest, readCatalog, readPreview, verifyPlan} from './configurator-data.ts';
import {StructureEditor} from './structure-editor.tsx';
import {QuantumPreview} from './quantum-preview.tsx';
import {StructureDraftStore,activeVariant,draftToInputs,makeStructureVariant,readStructureEditor} from './structure-editor-data.ts';
import type {Catalog, CatalogTemplate, FormFields, PlanSelection, Preview} from './configurator-data.ts';

function ReadOnly({title,value}:{title:string;value:unknown}) {
  return <details><summary>{title}</summary><pre>{value===undefined?'unknown — данные отсутствуют':JSON.stringify(value,null,2)}</pre></details>;
}
function Identity({template}:{template:CatalogTemplate}) {
  return <div className="configurator-identity"><p>{template.description}</p><dl>
    <dt>Модель / структура / материалы</dt><dd>{template.model.name} · <code>{template.model.path}</code> · SHA-256 <code>{template.model.sha256}</code></dd>
    <dt>Протокол</dt><dd>{template.protocol.name} · версия <code>{template.protocol.version}</code> · SHA-256 <code>{template.protocol.sha256}</code></dd>
    <dt>Template</dt><dd><code>{template.study}</code> · SHA-256 <code>{template.study_sha256}</code></dd>
  </dl></div>;
}
export function ConfiguratorPreview({preview}:{preview:Preview}) {
  return <article className="configurator-preview" aria-label="Preview Runner">
    <h4>План; attempt ещё не создан</h4>
    <Identity template={preview.template}/>
    <dl><dt>Research source</dt><dd><code>{preview.catalog_source_id}</code></dd>
      <dt>SHA-256 запроса / semantic draft</dt><dd><code>{preview.draft_sha256}</code><br/><code>{preview.semantic_draft_sha256}</code></dd>
      <dt>Exact plan SHA-256</dt><dd><code>{preview.plan_sha256}</code></dd>
      <dt>Fingerprint / scientific fingerprint</dt><dd><code>{preview.fingerprint}</code><br/><code>{preview.scientific_fingerprint}</code></dd>
      <dt>Points / executions</dt><dd>{preview.counts.points} / {preview.counts.executions}</dd></dl>
    <p>Preview не создаёт run или attempt. Execution, сходимость, физические проверки, дискретизация и экспериментальная валидация: unknown.</p>
    <ReadOnly title="Explicit variant diffs: comparison_kind / controlled_paths / overrides" value={preview.variant_diffs}/>
    <ReadOnly title="Resolved inputs и полные ordered assigning histories Runner" value={preview.resolved_inputs}/>
    <ReadOnly title="Typed used/default values, units, rule sources и point_sections Runner" value={preview.effective_defaults}/>
    <ReadOnly title="Exact owning default_rule_sources (Runner / Core)" value={preview.default_rule_sources}/>
    <p>Отсутствующий origin/history остаётся unknown; null означает отсутствие значения. UI не выводит источник сравнением с defaults.</p>
    <ReadOnly title="Оценки ресурсов / нижняя граница output (не admission)" value={preview.estimates}/>
    <p>Estimates не являются измерением RAM/CPU, свежим admission или разрешением ресурсов. Запрашиваемые ресурсы подтверждаются отдельно в существующей форме отправки.</p>
    <ReadOnly title="Validated structure_editor Runner: canonical inputs / geometry / bindings (не quantum solve)" value={preview.structure_editor}/>
    <ReadOnly title="Runner short_form projection" value={preview.short_form}/>
    <details><summary>Canonical semantic YAML Runner (read-only)</summary><pre>{preview.draft_yaml}</pre></details>
    <details><summary>Exact frozen plan: все inclusions / executions / operations / variants / points / axes / explicit predecessors (read-only)</summary><pre>{preview.plan}</pre></details>
  </article>;
}
function exportText(raw:string,name:string,type:string) {
  const url=URL.createObjectURL(new Blob([raw],{type}));
  try {const link=document.createElement('a');link.href=url;link.download=name;link.click();}finally{URL.revokeObjectURL(url);}
}
export function Configurator({token,context,availability,onPlan}:{token:string;context:string;availability?:unknown;onPlan:(value:PlanSelection|null)=>void}) {
  const [catalog,setCatalog]=useState<Catalog|null>(null),[id,setId]=useState('');
  const [mode,setMode]=useState<'form'|'yaml'>('form'),[yaml,setYaml]=useState('');
  const [temperature,setTemperature]=useState(''),[voltage,setVoltage]=useState('');
  const [voltageMode,setVoltageMode]=useState(''),[coupling,setCoupling]=useState('');
  const [variantEditor,setVariantEditor]=useState<'none'|'numerical'|'structure'>('none'),[variantId,setVariantId]=useState('');
  const [structureVariantId,setStructureVariantId]=useState('');
  const structure=useRef(new StructureDraftStore()),templateVariantIds=useRef<string[]>(['baseline']);
  const [,bumpStructure]=useState(0);
  const [comparison,setComparison]=useState(''),[paths,setPaths]=useState(''),[numerical,setNumerical]=useState<Record<string,string>>({});
  const [storedPreview,setPreview]=useState<Preview|null>(null),[previewContext,setPreviewContext]=useState(''),[reviewed,setReviewed]=useState(false);
  const [busy,setBusy]=useState(false),[loading,setLoading]=useState(true),[error,setError]=useState('');
  const [catalogRevision,setCatalogRevision]=useState(0);
  const session=useRef(new PreviewSession()),catalogSession=useRef(new PreviewSession()),previewLatch=useRef(false),fileGeneration=useRef(0);
  const currentContext=useRef(context);currentContext.current=context;
  const currentToken=useRef(token);currentToken.current=token;
  const selected=catalog?.templates.find(t=>t.id===id);
  const structureKey=JSON.stringify([token,catalog?.catalog_source_id,selected?.id,selected?.study_sha256,selected?.model]);
  const previousStructureRevision=structure.current.revision;structure.current.setContext(structureKey);
  if(structure.current.revision!==previousStructureRevision)templateVariantIds.current=['baseline'];
  const preview=previewContext===context?storedPreview:null;
  function invalidate(){session.current.invalidate();fileGeneration.current++;setPreview(null);setReviewed(false);setError('');onPlan(null);}
  useEffect(()=>{
    invalidate();setBusy(false);
    // Parent synchronous context invalidation also closes the pre-effect window.
  },[context]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(()=>{
    const g=catalogSession.current.begin();setLoading(true);setCatalog(null);setId('');onPlan(null);
    void catalogSession.current.receive(g,async()=>readCatalog(await api<unknown>(token,'/configurator/catalog')),value=>{
      setCatalog(value);setId(value.templates[0].id);setLoading(false);
    },err=>{setError(errorMessage(err));setLoading(false);});
    return ()=>{catalogSession.current.invalidate();session.current.invalidate();fileGeneration.current++;onPlan(null);};
  },[token,catalogRevision]); // eslint-disable-line react-hooks/exhaustive-deps
  async function showPreview(){
    if(previewLatch.current||!catalog||!selected)return;
    invalidate();previewLatch.current=true;setBusy(true);
    const g=session.current.begin(),snapshot=context,auth=token,sourceRevision=structure.current.revision;
    let requestedVariantId:string|undefined;
    await session.current.receive(g,async()=>{
      const fields:FormFields={};
      if(mode==='form') {
        if(temperature!=='')fields.temperature_K=Number(temperature);
        if(voltage!=='')fields.voltage_per_period_mV=Number(voltage);
        if(voltageMode!=='')fields.voltage_mode=voltageMode as 'independent'|'strict';
        if(coupling!=='')fields.scba_to_poisson=coupling;
        const active=activeVariant(variantEditor,()=>{requestedVariantId=variantId;return makeNumericalVariant(variantId,comparison,paths,numerical);},()=>{
          const bound=structure.current.bound;
          if(!bound)throw new Error('Нужен canonical structure DTO: сначала preview без нового variant, затем явно выберите execution.');
          requestedVariantId=structureVariantId;return makeStructureVariant(structureVariantId,draftToInputs(bound.draft),templateVariantIds.current);
        });
        if(active!==undefined)fields.variant=active;
      }
      const request=makeRequest(mode,id,catalog.catalog_source_id,fields,yaml);
      const value=readPreview(await api<unknown>(token,'/configurator/preview',request),catalog.catalog_source_id,id);
      await verifyPlan(value.plan,value.plan_sha256);return value;
    },value=>{if(snapshot===currentContext.current&&auth===currentToken.current&&sourceRevision===structure.current.revision){
      const capability=readStructureEditor(value.structure_editor,{catalog_source_id:catalog.catalog_source_id,template_id:id,template_sha256:selected.study_sha256,model:selected.model});
      structure.current.offer(capability);
      if(capability.status==='available')templateVariantIds.current=[...new Set(capability.executions.map(e=>e.variant_id).filter(v=>v!==requestedVariantId))];
      setPreviewContext(snapshot);setPreview(value);
    }},err=>{if(snapshot===currentContext.current&&auth===currentToken.current)setError(errorMessage(err));});
    previewLatch.current=false;setBusy(false);
  }
  async function readYaml(file:File|undefined){
    invalidate();if(!file)return;const g=++fileGeneration.current;
    try {if(file.size>131072)throw new Error('YAML превышает 128 KiB');const raw=await file.text();if(g!==fileGeneration.current)return;setYaml(raw);setMode('yaml');}
    catch(err){if(g===fileGeneration.current)setError(errorMessage(err));}
  }
  function switchMode(next:'form'|'yaml') {
    if(next===mode)return;
    if(next==='form'&&yaml!=='') {setError('YAML остаётся authoritative: возврат к форме может потерять expert values. Сохраните original YAML; для новой формы явно сбросьте draft.');return;}
    if(next==='yaml'&&preview)setYaml(preview.draft_yaml);
    invalidate();setMode(next);
  }
  return <section className="configurator" aria-label="Конфигуратор научной постановки">
    <h3>Новая постановка / explicit variant</h3>
    <p>Единый resolver Runner. Не изменяет существующие runs или активный SCI4. Оптика и spectrum здесь не запускаются.</p>
    <ReadOnly title="Planner runtime availability / version / source binding" value={availability}/>
    <p>Runtime version: {availability&&typeof availability==='object'&&'planner_version' in availability?String(availability.planner_version??'unknown'):'unknown'}</p>
    {loading&&<p role="status">Чтение каталога…</p>}
    {error&&<p role="alert" className="error">{error}</p>}
    {!catalog&&!loading&&<p role="status">Planner/catalog недоступен. Готовый raw plan и чтение прежних runs доступны в основной форме.</p>}
    <button type="button" disabled={loading||busy} onClick={()=>{invalidate();setCatalogRevision(v=>v+1);}}>Перечитать catalog/source</button>
    {catalog&&<>
      <p>Exact Research source: <code>{catalog.catalog_source_id}</code></p>
      <label>Задача / template<select value={id} disabled={busy} onChange={e=>{invalidate();setId(e.target.value);}}>{catalog.templates.map(t=><option key={t.id} value={t.id}>{t.name}</option>)}</select></label>
      {selected&&<Identity template={selected}/>}
      <div className="configurator-actions"><button type="button" aria-pressed={mode==='form'} onClick={()=>switchMode('form')}>Короткая форма</button><button type="button" aria-pressed={mode==='yaml'} onClick={()=>switchMode('yaml')}>Полный YAML expert</button>
        <button type="button" onClick={()=>{invalidate();setMode('form');setYaml('');setTemperature('');setVoltage('');setVoltageMode('');setCoupling('');setVariantEditor('none');setVariantId('');setStructureVariantId('');structure.current.reset();bumpStructure(v=>v+1);setComparison('');setPaths('');setNumerical({});}}>Явно сбросить draft к template</button></div>
      {mode==='form'?<div className="form-grid">
        <label>T (K), пусто = template<input type="number" step="any" value={temperature} onChange={e=>{invalidate();setTemperature(e.target.value);}}/></label>
        <label>V/period (mV), пусто = template<input type="number" step="any" value={voltage} onChange={e=>{invalidate();setVoltage(e.target.value);}}/></label>
        <label>Voltage mode<select value={voltageMode} onChange={e=>{invalidate();setVoltageMode(e.target.value);}}><option value="">Из template</option><option value="independent">independent</option><option value="strict">strict</option></select></label>
        <label>scba_to_poisson, пусто = template<input value={coupling} onChange={e=>{invalidate();setCoupling(e.target.value);}} placeholder="Existing Runner policy name"/></label>
        <label className="wide">Активный variant editor<select value={variantEditor} onChange={e=>{invalidate();setVariantEditor(e.target.value as 'none'|'numerical'|'structure');}}><option value="none">Без нового variant</option><option value="numerical">Numerical counts</option><option value="structure">Structure / physical_parameter</option></select></label>
        <p className="wide">Отправляется ровно выбранный editor: {variantEditor}. Буферы numerical и structure сохраняются при переключении. Combined expert variant задаётся полным YAML.</p>
        {variantEditor==='numerical'&&<>
          <label>Новый variant ID<input value={variantId} onChange={e=>{invalidate();setVariantId(e.target.value);}}/></label>
          <label>comparison_kind<input value={comparison} onChange={e=>{invalidate();setComparison(e.target.value);}}/></label>
          <label className="wide">controlled_paths: один existing path на строку<textarea value={paths} onChange={e=>{invalidate();setPaths(e.target.value);}}/></label>
          {['energy_nodes','momentum_nodes','spatial_nodes','basis_states'].map(key=><label key={key}>numerical.{key}, пусто = без override<input type="number" step="1" value={numerical[key]??''} onChange={e=>{invalidate();setNumerical({...numerical,[key]:e.target.value});}}/></label>)}
          <p className="wide">Другие explicit overrides доступны в полном YAML expert. Форма добавляет только указанные numerical counts; controlled_paths и comparison_kind задаются явно.</p>
          <p className="wide">Scope, единицы и допустимость отличий проверяет Runner. Сетка и physics автоматически не сокращаются.</p>
        </>}
        {variantEditor==='structure'&&<div className="wide"><label>Новый structure variant ID<input value={structureVariantId} onChange={e=>{invalidate();setStructureVariantId(e.target.value);}}/></label>
          <StructureEditor key={structure.current.revision} capability={structure.current.latest} bound={structure.current.bound}
            onSelect={executionId=>{invalidate();structure.current.select(executionId);bumpStructure(v=>v+1);}}
            onEdit={draft=>{invalidate();structure.current.edit(draft);bumpStructure(v=>v+1);}}/>
        </div>}
      </div>:<>
        <p>Полный qcl-negf-study-v2, включая configuration.overrides, variants, axes и policies. Original UTF-8 текст хранится без нормализации. Unsupported input получает явный отказ Runner.</p>
        <label>Original authored YAML<input type="file" accept=".yaml,.yml,text/yaml" onChange={e=>void readYaml(e.target.files?.[0])}/><textarea rows={18} value={yaml} onChange={e=>{invalidate();setYaml(e.target.value);}}/></label>
        <button type="button" onClick={()=>exportText(yaml,'draft-original.yaml','text/yaml;charset=utf-8')}>Скачать original YAML</button>
      </>}
      <div className="configurator-actions"><button type="button" className="primary" disabled={busy||loading||(mode==='yaml'&&!yaml)} onClick={()=>void showPreview()}>{busy?'Runner строит preview…':'Показать план (без запуска)'}</button></div>
      {preview&&<>
        <ConfiguratorPreview preview={preview}/>
        <QuantumPreview token={token} plan={preview.plan} sha256={preview.plan_sha256} parentContext={context}/>
        {mode==='form'&&<button type="button" onClick={()=>{setYaml(preview.draft_yaml);invalidate();setMode('yaml');}}>Продолжить в semantic YAML Runner</button>}
        <button type="button" onClick={()=>exportText(preview.plan,'frozen-plan.json','application/json')}>Скачать exact frozen plan</button>
        <label><input type="checkbox" checked={reviewed} onChange={e=>{setReviewed(e.target.checked);if(!e.target.checked)onPlan(null);}}/> Проверены scientific inputs, variant scope и выбранный Code / ресурсы / label ниже.</label>
        <button type="button" disabled={!reviewed||busy} onClick={()=>onPlan({plan:preview.plan,sha256:preview.plan_sha256})}>Передать exact frozen plan в форму отправки</button>
        <p>Отправка выполняется отдельной кнопкой Submit to AiiDA. Любое изменение draft/template/source/Code/ресурсов/label снимает подтверждение. Связь с research card создаётся вручную после известного успешного submit через существующее действие attach.</p>
      </>}
    </>}
  </section>;
}
