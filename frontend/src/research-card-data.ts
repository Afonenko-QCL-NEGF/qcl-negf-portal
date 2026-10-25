/** Detached native metadata only; titles/prose never infer membership. */
export interface ResearchCard {
  schema: 'qcl-negf-research-card-v1'; title: string; goal: string; question: string;
}
export interface CardVersion {
  card_uuid: string; version_uuid: string; version: number; previous_version_uuid: string | null;
  title: string; bytes: number; sha256: string; ctime: string;
}
export interface CardRun {
  association_uuid: string; card_uuid: string; card_version_uuid: string; run_uuid: string;
  plan_fingerprint: string; root_kind: 'study' | 'meta'; root_definition_id: string; ctime: string;
}
export interface CardAnalysis {
  association_uuid: string; card_uuid: string; card_version_uuid: string;
  anchor_run_uuid: string; report_uuid: string; report_sha256: string; ctime: string;
}
export type CardReceipt = {kind: 'version'; receipt: CardVersion} |
  {kind: 'run'; receipt: CardRun} | {kind: 'analysis'; receipt: CardAnalysis};
export type CardMutation = CardReceipt & {status: 'stored' | 'replayed'};
export type CardRequest = (CardReceipt & {status: 'stored'}) | {status: 'not_found'};
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?![\s\S])/;
const hash = /^[0-9a-f]{64}(?![\s\S])/;
const object = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v);
const exact = (v: Record<string, unknown>, fields: string[]) => Object.keys(v).sort().join(',') === fields.sort().join(',');
const uid = (v: unknown): v is string => typeof v === 'string' && uuid.test(v);
const sha = (v: unknown): v is string => typeof v === 'string' && hash.test(v);
const text = (v: unknown): v is string => typeof v === 'string' && v.length > 0;
const time = (v: unknown) => text(v) && Number.isFinite(Date.parse(v));
function refused(): never { throw new Error('Некорректная квитанция исследования'); }
export function readCardVersion(v: unknown): CardVersion {
  if (!object(v) || !exact(v, ['card_uuid','version_uuid','version','previous_version_uuid','title','bytes','sha256','ctime']) ||
      !uid(v.card_uuid) || !uid(v.version_uuid) || !Number.isSafeInteger(v.version) || Number(v.version) < 1 ||
      !(v.previous_version_uuid === null || uid(v.previous_version_uuid)) || !text(v.title) ||
      !Number.isSafeInteger(v.bytes) || Number(v.bytes) < 0 || Number(v.bytes) > 65536 || !sha(v.sha256) || !time(v.ctime) ||
      (v.version === 1 && (v.card_uuid !== v.version_uuid || v.previous_version_uuid !== null)) ||
      (Number(v.version) > 1 && (!uid(v.previous_version_uuid) || v.previous_version_uuid === v.version_uuid))) return refused();
  return v as unknown as CardVersion;
}
export function readCard(v: unknown): ResearchCard {
  if (!object(v) || !exact(v, ['schema','title','goal','question']) || v.schema !== 'qcl-negf-research-card-v1' ||
      !text(v.title) || !text(v.goal) || !text(v.question)) return refused();
  return v as unknown as ResearchCard;
}
export function readCardRun(v: unknown): CardRun {
  if (!object(v) || !exact(v, ['association_uuid','card_uuid','card_version_uuid','run_uuid','plan_fingerprint','root_kind','root_definition_id','ctime']) ||
      !uid(v.association_uuid) || !uid(v.card_uuid) || !uid(v.card_version_uuid) || !uid(v.run_uuid) ||
      !sha(v.plan_fingerprint) || !['study','meta'].includes(String(v.root_kind)) || !text(v.root_definition_id) || !time(v.ctime)) return refused();
  return v as unknown as CardRun;
}
export function readCardAnalysis(v: unknown): CardAnalysis {
  if (!object(v) || !exact(v, ['association_uuid','card_uuid','card_version_uuid','anchor_run_uuid','report_uuid','report_sha256','ctime']) ||
      !uid(v.association_uuid) || !uid(v.card_uuid) || !uid(v.card_version_uuid) || !uid(v.anchor_run_uuid) ||
      !uid(v.report_uuid) || !sha(v.report_sha256) || !time(v.ctime)) return refused();
  return v as unknown as CardAnalysis;
}
export function readCardRequest(v: unknown): CardRequest | CardMutation {
  if (!object(v)) return refused();
  if (v.status === 'not_found' && exact(v, ['status'])) return {status: 'not_found'};
  if (!['stored','replayed'].includes(String(v.status)) || !exact(v, ['status','kind','receipt'])) return refused();
  if (v.kind === 'version') readCardVersion(v.receipt);
  else if (v.kind === 'run') readCardRun(v.receipt);
  else if (v.kind === 'analysis') readCardAnalysis(v.receipt);
  else return refused();
  return v as unknown as CardMutation;
}
/** Increment synchronously on every selection, including before the next fetch. */
export function selectionGeneration() {
  let value = 0;
  return {next: () => ++value, current: (candidate: number) => value === candidate};
}


/** A lookup may only publish under the request UUID that started it. */
export async function lookupCardRequest(
  requestUuid: string, current: () => boolean, read: (uuid: string) => Promise<unknown>,
  returned: (value: CardRequest | CardMutation) => void, failed: (error: unknown) => void,
): Promise<void> {
  try {
    const value = readCardRequest(await read(requestUuid));
    if (current()) returned(value);
  } catch (error) { if (current()) failed(error); }
}
