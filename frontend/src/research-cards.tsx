import {useEffect, useRef, useState, type FormEvent} from 'react';
import {api, ApiError, downloadResearchCard, errorMessage, type Run, type AgentReport, type AgentReportReceipt} from './api';
import {readCard, readCardVersion, readCardRun, readCardAnalysis, readCardRequest, selectionGeneration, lookupCardRequest,
  type ResearchCard, type CardVersion, type CardRun, type CardAnalysis} from './research-card-data';

const blank = (): ResearchCard => ({schema:'qcl-negf-research-card-v1',title:'',goal:'',question:''});
function Pager({offset, count, change}: {offset: number; count: number; change: (n: number) => void}) {
  return <div className="card-actions"><button disabled={!offset} onClick={() => change(offset - 20)}>Назад</button>
    <span>Страница {offset / 20 + 1}</span><button disabled={count < 20} onClick={() => change(offset + 20)}>Далее</button></div>;
}

export function CanonicalCard({card, version}: {card: ResearchCard; version: CardVersion}) {
  return <article><p>Версия канонического вопроса <code>{version.version_uuid}</code></p>
    <h3>Канонический вопрос этой версии</h3><pre>{card.question}</pre>
    <h3>Цель</h3><pre>{card.goal}</pre></article>;
}
export function ResearchCardReport({report}: {report: AgentReport}) {
  return <article><h3>Отдельный author question_snapshot</h3><pre>{report.question_snapshot}</pre>
    <h3>Заключение автора</h3><pre>{report.conclusion}</pre><h3>Обоснование автора</h3><pre>{report.reasoning}</pre>
    <h3>Ограничения автора</h3><pre>{report.limitations}</pre><h3>Точные used_runs отчёта</h3><pre>{JSON.stringify(report.used_runs,null,2)}</pre></article>;
}

export function ResearchCards({token, onSelectRun}: {token: string; onSelectRun: (uuid: string) => void}) {
  const [cards, setCards] = useState<CardVersion[]>([]), [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState('');
  const [head, setHead] = useState<CardVersion | null>(null), [version, setVersion] = useState<CardVersion | null>(null);
  const [card, setCard] = useState<ResearchCard | null>(null), [draft, setDraft] = useState(blank);
  const [versions, setVersions] = useState<CardVersion[]>([]), [versionOffset, setVersionOffset] = useState(0);
  const [runs, setRuns] = useState<CardRun[]>([]), [analyses, setAnalyses] = useState<CardAnalysis[]>([]);
  const [relationOffset, setRelationOffset] = useState(0);
  const [runInput, setRunInput] = useState(''), [inspected, setInspected] = useState<Run | null>(null);
  const [reports, setReports] = useState<AgentReportReceipt[]>([]), [reportOffset, setReportOffset] = useState(0);
  const [pickedReport, setPickedReport] = useState<AgentReportReceipt | null>(null);
  const [report, setReport] = useState<AgentReport | null>(null);
  const [busy, setBusy] = useState(false), [loading, setLoading] = useState(false), [error, setError] = useState('');
  const [requestId, setRequestId] = useState(''), [diagnostic, setDiagnostic] = useState('');
  const [unknown, setUnknown] = useState(false), [readback, setReadback] = useState('');
  const generation = useRef(selectionGeneration()), runGeneration = useRef(selectionGeneration());
  const reportGeneration = useRef(selectionGeneration());
  const catalogGeneration = useRef(selectionGeneration());
  const requestGeneration = useRef(selectionGeneration());
  const writeLatch = useRef(false), uncertain = useRef(false);

  async function catalogue(page = offset) {
    const g = catalogGeneration.current.next();
    try {
      const value = await api<{cards: unknown[]}>(token, `/research-cards?limit=20&offset=${page}`);
      const rows = value.cards.map(readCardVersion);
      if (catalogGeneration.current.current(g)) { setCards(rows); setOffset(page); }
    } catch (err) { if (catalogGeneration.current.current(g)) setError(errorMessage(err)); }
  }
  useEffect(() => {
    void catalogue(0);
    return () => {
      generation.current.next(); runGeneration.current.next(); reportGeneration.current.next(); catalogGeneration.current.next(); requestGeneration.current.next();
    };
  }, [token]);

  function resetSelection() {
    setHead(null); setVersion(null); setCard(null); setDraft(blank()); setVersions([]);
    setRuns([]); setAnalyses([]); setVersionOffset(0); setRelationOffset(0); setError('');
    setPickedReport(null); setReport(null); reportGeneration.current.next();
    runGeneration.current.next(); setInspected(null); setReports([]); setReportOffset(0);
  }
  async function selectCard(uuid: string) {
    const g = generation.current.next(); resetSelection(); setSelected(uuid);
    if (!uuid) { setLoading(false); return; }
    setLoading(true);
    try {
      const [value, history, linkedRuns, linkedAnalyses] = await Promise.all([
        api<{head: unknown; card: unknown}>(token, `/research-cards/${uuid}`),
        api<{versions: unknown[]}>(token, `/research-cards/${uuid}/versions?limit=20&offset=0`),
        api<{runs: unknown[]}>(token, `/research-cards/${uuid}/runs?limit=20&offset=0`),
        api<{analyses: unknown[]}>(token, `/research-cards/${uuid}/analyses?limit=20&offset=0`),
      ]);
      const receipt = readCardVersion(value.head), body = readCard(value.card);
      if (receipt.card_uuid !== uuid) throw new Error('Чужая карточка');
      const hs = history.versions.map(readCardVersion), rs = linkedRuns.runs.map(readCardRun), ars = linkedAnalyses.analyses.map(readCardAnalysis);
      if ([...hs, ...rs, ...ars].some(r => r.card_uuid !== uuid)) throw new Error('Чужая связь');
      if (!generation.current.current(g)) return;
      setHead(receipt); setVersion(receipt); setCard(body); setDraft({...body});
      setVersions(hs); setRuns(rs); setAnalyses(ars);
    } catch (err) { if (generation.current.current(g)) setError(errorMessage(err)); }
    finally { if (generation.current.current(g)) setLoading(false); }
  }
  async function selectVersion(receipt: CardVersion) {
    const g = generation.current.next(); reportGeneration.current.next(); runGeneration.current.next(); setInspected(null); setReports([]); setPickedReport(null); setReport(null); setVersion(null); setCard(null); setDraft(blank()); setLoading(true); setError('');
    try {
      const value = await api<{version: unknown; card: unknown}>(token, `/research-cards/${selected}/versions/${receipt.version_uuid}`);
      const v = readCardVersion(value.version), c = readCard(value.card);
      if (v.card_uuid !== selected || v.version_uuid !== receipt.version_uuid) throw new Error('Чужая версия');
      if (!generation.current.current(g)) return;
      setVersion(v); setCard(c); setDraft({...c});
    } catch (err) { if (generation.current.current(g)) setError(errorMessage(err)); }
    finally { if (generation.current.current(g)) setLoading(false); }
  }
  async function history(page: number) {
    const g = generation.current.next(); setLoading(true); setError('');
    try {
      const value = await api<{versions: unknown[]}>(token, `/research-cards/${selected}/versions?limit=20&offset=${page}`);
      const rows = value.versions.map(readCardVersion);
      if (rows.some(r => r.card_uuid !== selected)) throw new Error('Чужая версия');
      if (generation.current.current(g)) { setVersions(rows); setVersionOffset(page); }
    } catch (err) { if (generation.current.current(g)) setError(errorMessage(err)); }
    finally { if (generation.current.current(g)) setLoading(false); }
  }
  async function relations(page: number) {
    const g = generation.current.next(); setLoading(true); setError('');
    try {
      const [r, a] = await Promise.all([
        api<{runs: unknown[]}>(token, `/research-cards/${selected}/runs?limit=20&offset=${page}`),
        api<{analyses: unknown[]}>(token, `/research-cards/${selected}/analyses?limit=20&offset=${page}`),
      ]);
      const rs = r.runs.map(readCardRun), ars = a.analyses.map(readCardAnalysis);
      if ([...rs,...ars].some(r => r.card_uuid !== selected)) throw new Error('Чужая связь');
      if (generation.current.current(g)) { setRuns(rs); setAnalyses(ars); setRelationOffset(page); }
    } catch (err) { if (generation.current.current(g)) setError(errorMessage(err)); }
    finally { if (generation.current.current(g)) setLoading(false); }
  }
  async function inspectRun(page = 0) {
    const g = runGeneration.current.next(); reportGeneration.current.next();
    setInspected(null); setReports([]); setPickedReport(null); setReport(null); setError('');
    try {
      const [r, reports] = await Promise.all([api<Run>(token, `/runs/${encodeURIComponent(runInput)}`),
        api<{reports: AgentReportReceipt[]}>(token, `/runs/${encodeURIComponent(runInput)}/agent-reports?limit=20&offset=${page}`)]);
      if (r.uuid !== runInput) throw new Error('Другой run UUID');
      if (!runGeneration.current.current(g)) return;
      setInspected(r); setReports(reports.reports); setReportOffset(page);
    } catch (err) { if (runGeneration.current.current(g)) setError(errorMessage(err)); }
  }
  async function viewReport(anchor: string, uuid: string, receipt: AgentReportReceipt | null) {
    const g = reportGeneration.current.next();
    setPickedReport(null); setReport(null); setError('');
    // Card switch invalidates this reader synchronously through reportGeneration.
    try {
      const value = await api<AgentReport>(token, `/runs/${anchor}/agent-reports/${uuid}`);
      if (value.anchor.run_uuid !== anchor) throw new Error('Другой anchor');
      if (!reportGeneration.current.current(g)) return;
      setReport(value); setPickedReport(receipt);
    } catch (err) { if (reportGeneration.current.current(g)) setError(errorMessage(err)); }
  }
  async function mutate(path: string, fields: Record<string, unknown>) {
    if (writeLatch.current || uncertain.current || unknown) return;
    writeLatch.current = true;
    const g = generation.current.next(), request_uuid = crypto.randomUUID();
    requestGeneration.current.next();
    setRequestId(request_uuid); setDiagnostic(''); setReadback(''); setBusy(true); setError('');
    try {
      const result = readCardRequest(await api<unknown>(token, path, {request_uuid, ...fields}));
      if (result.status === 'not_found') throw new Error('Квитанция сохранения отсутствует');
      setReadback(JSON.stringify(result, null, 2));
      if (generation.current.current(g)) {
        await catalogue();
        if (generation.current.current(g)) await selectCard(result.receipt.card_uuid);
      }
    } catch (err) {
      if (err instanceof ApiError) {
        setDiagnostic(`operation=${err.operationId ?? 'нет'}; outcome=${err.outcome ?? 'не указан'}; unresolved=${err.unresolvedOperationId ?? 'нет'}`);
        if (err.outcome === 'unknown' || err.unresolvedOperationId || (!err.outcome && ![404,409,422].includes(err.status))) {
          uncertain.current = true; setUnknown(true);
        }
      } else {
        // A lost transport response or malformed receipt can follow a store.
        uncertain.current = true; setUnknown(true);
        setDiagnostic('Ответ операции не получен; выполните явный readback request UUID.');
      }
      setError(errorMessage(err));
    } finally { writeLatch.current = false; setBusy(false); }
  }
  async function lookup() {
    if (!requestId) return;
    const request = requestId, lookupGeneration = requestGeneration.current.next();
    await lookupCardRequest(request, () => requestGeneration.current.current(lookupGeneration),
      uuid => api<unknown>(token, `/research-card-requests/${uuid}`),
      value => setReadback(value.status === 'not_found'
        ? 'Квитанция не найдена. Это не доказывает отсутствие эффекта и не разрешает повтор или снятие fence.'
        : JSON.stringify(value, null, 2)),
      err => setError(errorMessage(err)));
  }

  function save(event: FormEvent) {
    event.preventDefault();
    if (selected && (!version || version.version_uuid !== head?.version_uuid)) return;
    void mutate(selected ? `/research-cards/${selected}/versions` : '/research-cards', {
      card: JSON.stringify(draft), ...(selected ? {base_version_uuid: version!.version_uuid} : {}),
    });
  }
  return <section className="panel research-cards" aria-label="Исследования">
    <h2>Исследования</h2>
    <p>Канонический вопрос версионируется. Исторический run или отчёт без явной связи с карточкой не связан.</p>
    <div className="card-actions"><button onClick={() => void selectCard('')}>Новая карточка</button>
      <button onClick={() => void catalogue()}>Обновить список</button></div>
    <ul>{cards.map(c => <li key={c.card_uuid}><button onClick={() => void selectCard(c.card_uuid)}>{c.title} · v{c.version}</button> <code>{c.card_uuid}</code></li>)}</ul>
    {!cards.length && <p>На этой странице карточек нет.</p>}
    <Pager offset={offset} count={cards.length} change={p => void catalogue(p)} />
    {loading && <p role="status">Загрузка исследования…</p>}
    {error && <p role="alert" className="error">{error}</p>}
    {requestId && <div className="card-receipt"><p>request UUID <code>{requestId}</code></p><p>{diagnostic}</p>
      {unknown && <p role="alert">Исход операции неизвестен; повтор запрещён. Fence сохраняется даже после readback.</p>}
      <button onClick={() => void lookup()}>Прочитать квитанцию запроса</button><pre>{readback}</pre></div>}
    {(!selected || card) && <form onSubmit={save}>
      <label>Название<input required maxLength={128} value={draft.title} onChange={e => setDraft({...draft,title:e.target.value})} /></label>
      <label>Цель<textarea required value={draft.goal} onChange={e => setDraft({...draft,goal:e.target.value})} /></label>
      <label>Вопрос<textarea required value={draft.question} onChange={e => setDraft({...draft,question:e.target.value})} /></label>
      <button disabled={busy || loading || unknown || (!!selected && version?.version_uuid !== head?.version_uuid)}>
        {busy ? 'Сохранение…' : selected ? 'Сохранить новую версию' : 'Создать карточку'}</button>
    </form>}
    {version && card && <>
      <p>Card <code>{version.card_uuid}</code> · версия {version.version} <code>{version.version_uuid}</code></p>
      <p>SHA-256 <code>{version.sha256}</code> · {version.bytes} bytes</p>
      <CanonicalCard card={card} version={version} />
      <button onClick={() => void downloadResearchCard(token, selected, version.version_uuid).catch(e => setError(errorMessage(e)))}>Скачать исходный JSON</button>
      <h3>История версий</h3>
      <ul>{versions.map(v => <li key={v.version_uuid}><button disabled={loading} onClick={() => void selectVersion(v)}>v{v.version} · {v.title}</button> <code>{v.version_uuid}</code></li>)}</ul>
      <Pager offset={versionOffset} count={versions.length} change={p => void history(p)} />
      {version.version_uuid !== head?.version_uuid && <p>Историческая версия: для редактирования выберите текущую голову.</p>}
      <h3>Связанные frozen runs</h3>
      {!runs.length && <p>На этой странице явных связей нет.</p>}
      <ul>{runs.map(r => <li key={r.association_uuid}><button onClick={() => onSelectRun(r.run_uuid)}>Открыть варианты и attempts</button>
        <code>{r.run_uuid}</code> · pinned version <code>{r.card_version_uuid}</code> · FP <code>{r.plan_fingerprint}</code></li>)}</ul>
      <h3>Связанные анализы</h3>
      {!analyses.length && <p>На этой странице связанных анализов нет.</p>}
      <ul>{analyses.map(a => <li key={a.association_uuid}><button onClick={() => void viewReport(a.anchor_run_uuid,a.report_uuid,null)}>Читать отчёт</button>
        <code>{a.report_uuid}</code> · SHA-256 <code>{a.report_sha256}</code> · version <code>{a.card_version_uuid}</code></li>)}</ul>
      <Pager offset={relationOffset} count={Math.max(runs.length,analyses.length)} change={p => void relations(p)} />
      <h3>Явно связать run или сохранённый отчёт</h3>
      <label>Run UUID<input value={runInput} onChange={e => {
        runGeneration.current.next(); reportGeneration.current.next(); setRunInput(e.target.value);
        setInspected(null); setReports([]); setPickedReport(null); setReport(null);
      }} /></label>
      <button onClick={() => void inspectRun()}>Прочитать frozen run</button>
      {inspected && <div><p>Run <code>{inspected.uuid}</code> · frozen FP <code>{inspected.plan_fingerprint ?? 'неизвестен'}</code></p>
        <p>Привязка охватывает весь frozen run, все variants и observed attempts, включая failed/nonconverged. Научная приёмка не изменяется.</p>
        <p>Выбранная версия <code>{version.version_uuid}</code></p>
        <button disabled={busy || loading || unknown || !inspected.plan_fingerprint || inspected.plan_structure?.status !== 'available'} onClick={() => void mutate(`/research-cards/${selected}/runs`, {
          card_version_uuid:version.version_uuid,run_uuid:inspected.uuid,plan_fingerprint:inspected.plan_fingerprint,
        })}>Подтвердить связь всего frozen run</button>
        <ul>{reports.map(r => <li key={r.uuid}><button onClick={() => void viewReport(inspected.uuid,r.uuid,r)}>Выбрать точный отчёт</button><code>{r.uuid}</code> · <code>{r.sha256}</code></li>)}</ul>
        <Pager offset={reportOffset} count={reports.length} change={p => void inspectRun(p)} /></div>}
      {pickedReport && <div><p>Report UUID <code>{pickedReport.uuid}</code> · SHA-256 <code>{pickedReport.sha256}</code></p>
        <button disabled={busy || loading || unknown} onClick={() => void mutate(`/research-cards/${selected}/analyses`, {
          card_version_uuid:version.version_uuid,anchor_run_uuid:pickedReport.anchor.run_uuid,report_uuid:pickedReport.uuid,
        })}>Подтвердить связь отчёта с выбранной версией</button></div>}
      {report && <ResearchCardReport report={report} />}
    </>}
  </section>;
}
