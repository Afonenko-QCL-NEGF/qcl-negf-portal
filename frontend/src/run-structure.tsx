import { useState } from 'react';
import type { Run } from './api.ts';
import { inclusionTree, pointPage, readPlanStructure } from './run-structure-data.ts';
import type { PlanPoint } from './run-structure-data.ts';

function anchor(run: string, kind: string, id: string) {
  return `plan-${encodeURIComponent(run)}-${kind}-${encodeURIComponent(id)}`;
}
function PointTable({points, executionId}: {points: PlanPoint[]; executionId: string}) {
  const [page, setPage] = useState(0);
  const view = pointPage(points, page);
  return <div className="plan-points">
    <p>Точек всего: {view.total}. Страница {view.page + 1} из {view.pages}; до 100 строк.</p>
    <div className="plan-table-scroll"><table><thead><tr>
      <th>Явная зависимость</th><th>Branch / order</th><th>Initialization</th>
      <th>T (K)</th><th>V per period (V)</th><th>Состояние точки</th>
    </tr></thead><tbody>{view.rows.map(point => <tr key={point.id}>
      <td><code>{point.predecessor_id === null ? `${point.id} (без predecessor)` : `${point.predecessor_id} → ${point.id}`}</code></td>
      <td>{point.branch} / {point.order}</td><td>{point.initialization}</td>
      <td>{point.temperature_K}</td><td>{point.voltage_per_period_V}</td>
      <td>Объявлено в плане; выполнение и научная оценка: unknown</td>
    </tr>)}</tbody></table></div>
    {view.pages > 1 && <nav aria-label={`Страницы точек ${executionId}`}>
      <button type="button" disabled={view.page === 0} onClick={() => setPage(view.page - 1)}>Предыдущие 100</button>
      <button type="button" disabled={view.page + 1 === view.pages} onClick={() => setPage(view.page + 1)}>Следующие 100</button>
    </nav>}
  </div>;
}
export function RunStructure({run}: {run: Run}) {
  // Both this boundary and the parent detail key reset local pagination on run switch.
  return <StructureDetail key={run.uuid} run={run} />;
}
function StructureDetail({run}: {run: Run}) {
  const structure = readPlanStructure(run.plan_structure);
  const children = run.children ?? [];
  return <section className="run-structure" aria-label="Зависимости сохранённого плана">
    <h4>Зависимости сохранённого плана</h4>
    <p>Run UUID: <code>{run.uuid}</code><br />Fingerprint: <code>{run.plan_fingerprint ?? 'unknown'}</code></p>
    {!structure || structure.status === 'unavailable' ? <p role="status">Структура сохранённого плана недоступна: {structure?.status === 'unavailable' ? structure.reason : run.plan_structure === undefined ? 'producer_unavailable' : 'unsupported_structure'}</p> : <>
      <p>Root: {structure.root.kind} / <code>{structure.root.definition_id}</code></p>
      <p>«Объявлено в плане» — сохранённая структура. «Наблюдаемый attempt» — реальный CalcJob из inventory потомков, без утверждения о прямой CALL связи. Process/exit не определяют научную приёмку. Порядок строк не создаёт зависимость.</p>
      <h5>Дерево включений</h5>
      <ul className="plan-inclusions">{inclusionTree(structure.inclusions).map(({row, depth}) => <li key={row.id} id={anchor(run.uuid, 'inclusion', row.id)} style={{marginInlineStart: `${Math.min(depth, 20) * 1.25}rem`}}>
        <span>Уровень {depth}: </span><code>{row.id}</code> — {row.label}; definition: <code>{row.definition_id}</code>; path: {row.path.join(' / ')};
        {' parent: '}{row.parent_id === null ? 'root' : <a href={`#${anchor(run.uuid, 'inclusion', row.parent_id)}`}>{row.parent_id}</a>}
        <ul>{row.execution_ids.map(id => <li key={id}><a href={`#${anchor(run.uuid, 'execution', id)}`}>{id}</a> (объявлено в плане)</li>)}</ul>
      </li>)}</ul>
      {structure.executions.map(execution => {
        const attempts = children.filter(child => child.execution_id === execution.id);
        return <article key={execution.id} id={anchor(run.uuid, 'execution', execution.id)}>
          <h5>Execution <code>{execution.id}</code> — {execution.label}</h5>
          <p>Definition: <code>{execution.definition_id}</code>; variant: <code>{execution.variant_id}</code>; operation: <code>{execution.operation}</code>; voltage mode: <code>{execution.voltage_mode}</code></p>
          {attempts.length ? <ul>{attempts.map(child => <li key={child.uuid}><a href={`#${anchor(run.uuid, 'attempt', child.uuid)}`}>Наблюдаемый attempt {child.attempt}: {child.uuid}</a></li>)}</ul> : <p>attempt не создан</p>}
          <PointTable points={structure.points.filter(point => point.execution_id === execution.id)} executionId={execution.id} />
        </article>;
      })}
    </>}
    <h5>Все наблюдаемые attempts</h5>
    <p>Scientific assessment для каждого attempt: unknown. Selected result execution не переносится на отдельные attempts или точки.</p>
    {children.length ? <ul>{children.map(child => <li key={child.uuid} id={anchor(run.uuid, 'attempt', child.uuid)}>
      UUID: <code>{child.uuid}</code>; execution: <code>{child.execution_id}</code>; attempt: {child.attempt}; process: {child.process_state}; exit: {child.exit_status ?? 'unknown'}; scientific: unknown
    </li>)}</ul> : <p>Наблюдаемых attempts нет.</p>}
  </section>;
}
