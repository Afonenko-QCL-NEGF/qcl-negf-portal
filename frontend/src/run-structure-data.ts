/** Read-only presentation DTO, independent of results or current defaults. */
export interface PlanInclusion {
  id: string; parent_id: string | null; definition_id: string; label: string;
  path: string[]; execution_ids: string[];
}
export interface PlanExecution {
  id: string; definition_id: string; variant_id: string; label: string;
  operation: string; point_ids: string[]; voltage_mode: string;
}
export interface PlanPoint {
  id: string; execution_id: string; branch: string; order: number;
  initialization: string; predecessor_id: string | null;
  temperature_K: number; voltage_per_period_V: number;
}
export interface AvailableStructure {
  status: 'available'; root: {kind: 'study' | 'meta'; definition_id: string};
  inclusions: PlanInclusion[]; executions: PlanExecution[]; points: PlanPoint[];
}
export type PlanStructure = AvailableStructure | {
  status: 'unavailable'; reason: 'unsupported_structure' | 'unsupported_plan_schema';
};
type Row = Record<string, unknown>;
const object = (v: unknown): v is Row => !!v && typeof v === 'object' && !Array.isArray(v);
const text = (v: unknown): v is string => typeof v === 'string' && v.length > 0;
const label = (v: unknown): v is string => typeof v === 'string';
const nullableText = (v: unknown) => v === null || text(v);
const keys = (v: Row, expected: string[]) => Object.keys(v).length === expected.length && expected.every(k => Object.hasOwn(v, k));
function refs(v: unknown): v is string[] {
  return Array.isArray(v) && v.every(text) && new Set(v).size === v.length;
}
function rows(v: unknown, expected: string[]): v is Row[] {
  return Array.isArray(v) && v.every(r => object(r) && keys(r, expected) && text(r.id)) && new Set(v.map(r => r.id)).size === v.length;
}
/** Refuse malformed available DTOs without filling missing values or inferring edges. */
export function readPlanStructure(value: unknown): PlanStructure | null {
  if (!object(value)) return null;
  if (value.status === 'unavailable') {
    if (!keys(value, ['status', 'reason']) || (value.reason !== 'unsupported_structure' && value.reason !== 'unsupported_plan_schema')) return null;
    return value as unknown as PlanStructure;
  }
  if (value.status !== 'available' || !keys(value, ['status', 'root', 'inclusions', 'executions', 'points']) ||
      !object(value.root) || !keys(value.root, ['kind', 'definition_id']) ||
      (value.root.kind !== 'study' && value.root.kind !== 'meta') || !text(value.root.definition_id) ||
      !rows(value.inclusions, ['id', 'parent_id', 'definition_id', 'label', 'path', 'execution_ids']) ||
      !rows(value.executions, ['id', 'definition_id', 'variant_id', 'label', 'operation', 'point_ids', 'voltage_mode']) ||
      !rows(value.points, ['id', 'execution_id', 'branch', 'order', 'initialization', 'predecessor_id', 'temperature_K', 'voltage_per_period_V'])) return null;
  const inclusions = value.inclusions, executions = value.executions, points = value.points;
  const executionIds = new Set(executions.map(r => r.id));
  const parents = new Map<unknown, unknown>(), covered = new Set<string>();
  for (const r of inclusions) {
    if (!nullableText(r.parent_id) || !text(r.definition_id) || !label(r.label) ||
        !Array.isArray(r.path) || !r.path.length || !r.path.every(text) || !refs(r.execution_ids)) return null;
    parents.set(r.id, r.parent_id);
    for (const id of r.execution_ids) {
      if (!executionIds.has(id)) return null;
      covered.add(id);
    }
  }
  if (covered.size !== executionIds.size) return null;
  const finished = new Set<unknown>();
  for (const id of parents.keys()) {
    const chain = new Set<unknown>(); let current: unknown = id;
    while (current !== null && !finished.has(current)) {
      if (!parents.has(current) || chain.has(current)) return null;
      chain.add(current); current = parents.get(current);
    }
    for (const entry of chain) finished.add(entry);
  }
  const membership = new Map<unknown, Set<string>>();
  for (const r of executions) {
    if (!text(r.definition_id) || !text(r.variant_id) || !label(r.label) || !text(r.operation) || !text(r.voltage_mode) || !refs(r.point_ids)) return null;
    membership.set(r.id, new Set());
  }
  const previous = new Map<unknown, Row>();
  for (const p of points) {
    if (!text(p.execution_id) || !executionIds.has(p.execution_id) || !text(p.branch) || !text(p.initialization) ||
        !Number.isSafeInteger(p.order) || !nullableText(p.predecessor_id) ||
        typeof p.temperature_K !== 'number' || !Number.isFinite(p.temperature_K) ||
        typeof p.voltage_per_period_V !== 'number' || !Number.isFinite(p.voltage_per_period_V)) return null;
    if (p.predecessor_id !== null) {
      const parent = previous.get(p.predecessor_id);
      if (!parent || ['execution_id', 'branch', 'temperature_K'].some(k => parent[k] !== p[k])) return null;
    }
    previous.set(p.id, p); membership.get(p.execution_id)!.add(p.id as string);
  }
  for (const r of executions) {
    const actual = membership.get(r.id)!;
    const expected = r.point_ids as string[];
    if (actual.size !== expected.length || !expected.every(id => actual.has(id))) return null;
  }
  return value as unknown as AvailableStructure;
}
export const POINT_PAGE_SIZE = 100;
export function pointPage(points: PlanPoint[], requestedPage: number) {
  const pages = Math.max(1, Math.ceil(points.length / POINT_PAGE_SIZE));
  const page = Number.isSafeInteger(requestedPage) ? Math.max(0, Math.min(requestedPage, pages - 1)) : 0;
  return {page, pages, total: points.length, rows: points.slice(page * POINT_PAGE_SIZE, (page + 1) * POINT_PAGE_SIZE)};
}
/** Iterative preorder preserves a valid inclusion forest without recursive rendering. */
export function inclusionTree(rows: PlanInclusion[]) {
  const children = new Map<string | null, PlanInclusion[]>();
  for (const row of rows) {
    const siblings = children.get(row.parent_id);
    if (siblings) siblings.push(row); else children.set(row.parent_id, [row]);
  }
  const stack = (children.get(null) ?? []).slice().reverse().map(row => ({row, depth: 0}));
  const result: {row: PlanInclusion; depth: number}[] = [];
  while (stack.length) {
    const item = stack.pop()!; result.push(item);
    for (const row of (children.get(item.row.id) ?? []).slice().reverse()) stack.push({row, depth: item.depth + 1});
  }
  return result;
}
