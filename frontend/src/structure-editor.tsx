import {useState} from 'react';
import {applyPreset,changeLayer,draftGeometry,draftNumber} from './structure-editor-data.ts';
import type {BoundStructureDraft,StructureCapability,StructureDraft,LayerDraft} from './structure-editor-data.ts';
const layerFields:[keyof Omit<LayerDraft,'doped'>,string][]=[['thickness_nm','Толщина (nm)'],['material','Material label'],['aluminium_fraction','x_Al (доля)'],['conduction_band_edge_eV','E_c (eV)'],['longitudinal_relative_mass','m_z/m_e'],['transverse_relative_mass','m_parallel/m_e'],['relative_permittivity','epsilon_r']];
export function StructureGeometry({draft}:{draft:StructureDraft}){
 let geometry:ReturnType<typeof draftGeometry>;
 try{geometry=draftGeometry(draft);}catch(error){return <p role="status">{error instanceof Error?error.message:'Геометрия недоступна'}. Исходные controls сохранены.</p>;}
 const width=580,x=(v:number)=>50+v/geometry.period_nm*width;
 const interfaces=draft.interfaces_nm.map(v=>{try{return draftNumber(v);}catch{return null;}});
 const invalid=interfaces.some(v=>v===null||v<0||v>geometry.period_nm);
 return <div className="structure-geometry"><p>Черновик: Runner ещё не проверил. Один structural period {geometry.period_nm} nm до position-origin/bias sampling. Допированные области — только mask.</p>
 <svg viewBox="0 0 680 155" role="img" aria-label="Черновая геометрия одного периода, nm">
 {geometry.layers.map((layer,i)=><g key={i}><rect x={x(layer.left_nm)} y="30" width={(layer.right_nm-layer.left_nm)/geometry.period_nm*width} height="65" fill={draft.layers[i].doped?'#b8d6ee':'#eef4fa'} stroke="#31567a"><title>{`Слой ${i+1}: ${draft.layers[i].material}, ${layer.left_nm}…${layer.right_nm} nm; doped=${draft.layers[i].doped}`}</title></rect><text x={x(layer.left_nm)} y="116">{layer.left_nm}</text></g>)}
 {interfaces.map((v,i)=>v!==null&&v>=0&&v<=geometry.period_nm?<line key={i} x1={x(v)} x2={x(v)} y1="20" y2="100" stroke="#9b4820" strokeDasharray="4 3"><title>{`Явный interface ${i+1}: ${draft.interfaces_nm[i]} nm`}</title></line>:null)}
 <text x="590" y="140">{geometry.period_nm} nm</text></svg>
 {invalid&&<p role="alert">Есть неполные или вне периода interfaces. Исправьте их явно; окончательную допустимость определяет Runner.</p>}
 <p>После изменения геометрии interfaces остаются явно заданным списком и могут устареть. UI не заменяет их границами слоёв, не строит bias potential или объёмную donor density.</p></div>;
}
export function StructureEditor({capability,bound,onSelect,onEdit}:{capability:StructureCapability|null;bound:BoundStructureDraft|null;onSelect:(executionId:string)=>void;onEdit:(draft:StructureDraft)=>void}){
 const [error,setError]=useState('');
 function action(run:()=>void){try{setError('');run();}catch(err){setError(err instanceof Error?err.message:'Изменение недоступно');}}
 if(!capability)return <p role="status">Сначала получите preview template без нового variant. Затем выберите execution и создайте structure variant.</p>;
 if(capability.status==='unavailable')return <p role="status">Structure editor недоступен: {capability.reason}. Existing YAML и valid preview/plan доступны.</p>;
 const selected=bound&&capability.executions.some(e=>e.execution_id===bound.execution.execution_id)?bound.execution.execution_id:'';
 return <section className="structure-editor" aria-label="Редактор объявленной структуры"><h4>Слои и кусочно-постоянные свойства</h4>
 <label>Явно заменить черновик из execution / variant<select value={selected} onChange={e=>{if(e.target.value)action(()=>onSelect(e.target.value));}}><option value="">Выберите execution; draft не заменяется при refresh</option>{capability.executions.map(e=><option key={e.execution_id} value={e.execution_id}>{e.execution_id} · {e.variant_id}</option>)}</select></label>
 {error&&<p role="alert">{error}</p>}
 {bound&&<><p>Источник черновика: execution {bound.execution.execution_id}, variant {bound.execution.variant_id}, source {bound.execution.source_binding.catalog_source_id}; model SHA {bound.execution.source_binding.model.sha256}. {bound.dirty?'Есть локальные непроверенные edits.':'Скопированы canonical inputs последнего выбранного DTO.'}</p>
 <p>Presets — «набор выбранной модели», не измеренные material constants. Custom edits имеют unknown/research origin. Изменение x_Al не пересчитывает E_c, masses или epsilon. Для произвольного экспертного профиля нужен полный YAML и owning model support.</p>
 <StructureGeometry draft={bound.draft}/>
 <div className="structure-table"><table><caption>Submitted piecewise controls черновика, не effective profile или новые квантовые уровни</caption><thead><tr><th>Слой</th><th>d (nm)</th><th>Material</th><th>x_Al</th><th>E_c (eV)</th><th>m_z/m_e</th><th>m_parallel/m_e</th><th>epsilon_r</th><th>Doped mask</th></tr></thead><tbody>{bound.draft.layers.map((layer,i)=><tr key={i}><td>{i+1}</td><td>{layer.thickness_nm}</td><td>{layer.material}</td><td>{layer.aluminium_fraction}</td><td>{layer.conduction_band_edge_eV}</td><td>{layer.longitudinal_relative_mass}</td><td>{layer.transverse_relative_mass}</td><td>{layer.relative_permittivity}</td><td>{layer.doped?'true':'false'}</td></tr>)}</tbody></table></div>
 {bound.draft.layers.map((layer,i)=><fieldset key={i}><legend>Слой {i+1}</legend><div className="structure-fields">
 {layerFields.map(([key,label])=><label key={key}>{label}<input value={layer[key]} inputMode={key==='material'?'text':'decimal'} maxLength={key==='material'?256:64} onChange={e=>onEdit({...bound.draft,layers:bound.draft.layers.map((v,j)=>j===i?{...v,[key]:e.target.value}:{...v})})}/></label>)}
 <label><input type="checkbox" checked={layer.doped} onChange={e=>onEdit({...bound.draft,layers:bound.draft.layers.map((v,j)=>j===i?{...v,doped:e.target.checked}:{...v})})}/> Doped mask</label>
 <label>Скопировать полный tuple набора модели<select value="" onChange={e=>{const preset=bound.execution.material_presets[Number(e.target.value)];if(e.target.value!==''&&preset)action(()=>onEdit(applyPreset(bound.draft,i,preset)));}}><option value="">Custom / без автоматического изменения</option>{bound.execution.material_presets.map((p,j)=><option key={j} value={j}>{p.material} · x_Al={p.aluminium_fraction} · E_c={p.conduction_band_edge_eV} eV</option>)}</select></label></div>
 <div className="configurator-actions">{(['up','down','duplicate','remove','add'] as const).map(operation=><button type="button" key={operation} disabled={(operation==='up'&&i===0)||(operation==='down'&&i===bound.draft.layers.length-1)||(operation==='remove'&&bound.draft.layers.length===1)||((operation==='duplicate'||operation==='add')&&bound.draft.layers.length>=256)} onClick={()=>action(()=>onEdit(changeLayer(bound.draft,i,operation)))}>{({up:'Вверх',down:'Вниз',duplicate:'Дублировать',remove:'Удалить',add:'Добавить копию выбранного слоя ниже'})[operation]}</button>)}</div></fieldset>)}
 <div className="structure-fields">{([['donor_sheet_density_per_m2','Глобальная donor sheet density (m^-2)'],['ionization_fraction','Ionization fraction'],['position_origin_nm','Position origin (nm)'],['reference_energy_eV','Reference energy (eV)']] as const).map(([key,label])=><label key={key}>{label}<input value={bound.draft[key]} inputMode="decimal" maxLength={64} onChange={e=>onEdit({...bound.draft,[key]:e.target.value})}/></label>)}</div>
 <label>Явные interfaces (nm), по одному числу на строку; пустой список разрешает проверить Runner<textarea rows={5} value={bound.draft.interfaces_nm.join('\n')} onChange={e=>{const values=e.target.value===''?[]:e.target.value.split('\n');if(values.length>256){setError('Не более 256 interfaces; текст не принят');return;}onEdit({...bound.draft,interfaces_nm:values});}}/></label>
 <details><summary>Bound source и actual assigning histories исходного tuple</summary><pre>{JSON.stringify(bound.execution.source_binding,null,2)}</pre></details>
 <p>Никакого quantum solve, интерполяции material constants или continuous donor profile. Новый period может изменить effective field при фиксированном V/period только через owning Runner; это физический variant.</p></>}
 </section>;
}
