/** Defensive G1 inventory presentation. None of these bytes prove a run binding. */
import { fragmentIdPattern, type DocumentationAvailability } from './api.ts';
export class RequestEpoch {
  private sequence = 0;
  next(): number { return ++this.sequence; }
  invalidate(): void { this.sequence++; }
  current(ticket: number): boolean { return ticket === this.sequence; }
}
export interface Descriptor { id: string; file: string; sha256: string }
interface DocRef { section: string; path: string; anchor: string | null }
export interface ReferenceFragment {
  format: string; id: string; source_revision: string; title: string; summary: string;
  scope: string; full: DocRef; related: DocRef[]; assumptions: string[]; limitations: string[];
}
function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Некорректная справка');
  return value as Record<string, unknown>;
}
function revision(value: string): void {
  if (!/^[0-9a-f]{40}(?![\s\S])/.test(value)) throw new Error('Некорректная версия справки');
}
function relative(path: string): boolean {
  return typeof path === 'string' && /^[A-Za-z0-9_-][A-Za-z0-9_./-]*(?![\s\S])/.test(path) &&
    path.split('/').every(p=>p.length > 0 && !p.startsWith('.'));
}
export function docHref(rev: string, value: unknown): string {
  revision(rev); const d=object(value);
  if (typeof d.section !== 'string' || !['core','runner','research','operator'].includes(d.section) ||
      typeof d.path !== 'string' || !relative(d.path) ||
      !d.path.endsWith('.html') || !(d.anchor === null || typeof d.anchor === 'string' && /^[A-Za-z0-9_.:-]+(?![\s\S])/.test(d.anchor)))
    throw new Error('Некорректная ссылка справки');
  return `/docs/${rev}/${d.section}/${d.path}${d.anchor === null ? '' : `#${d.anchor}`}`;
}
export function checkedBundle(value: unknown, rev: string): Descriptor[] {
  revision(rev); const b=object(value);
  if (b.format !== 'qcl-negf.docs-bundle.v1' || object(b.sources).revision !== rev ||
      !Array.isArray(b.fragments) || b.fragments.length > 256) throw new Error('Некорректный bundle справки');
  const ids=new Set<string>();
  return b.fragments.map(value=>{
    const d=object(value);
    if(typeof d.id !== 'string' || !fragmentIdPattern.test(d.id) || ids.has(d.id) ||
       d.file !== `help/fragments/${d.id}.json` || typeof d.sha256 !== 'string' || !/^[0-9a-f]{64}(?![\s\S])/.test(d.sha256))
      throw new Error('Некорректный descriptor справки');
    ids.add(d.id); return d as unknown as Descriptor;
  });
}
export function checkedFragment(value: unknown, rev: string, id: string): ReferenceFragment {
  const f=object(value); revision(rev);
  if (f.format !== 'qcl-negf.model-help-fragment.v1' || f.id !== id || f.source_revision !== rev ||
      typeof f.title !== 'string' || f.title.length > 160 || typeof f.summary !== 'string' || f.summary.length > 1600 ||
      !['baseline','variant','operator_only','outside_study'].includes(String(f.scope))) throw new Error('Некорректный фрагмент справки');
  const source=object(f.meaning_source);
  if(typeof source.component !== 'string' || typeof source.path !== 'string' || !relative(source.path) ||
      typeof source.revision !== 'string' || !/^[0-9a-f]{40}(?![\s\S])/.test(source.revision) ||
      typeof source.sha256 !== 'string' || !/^[0-9a-f]{64}(?![\s\S])/.test(source.sha256) ||
      typeof object(source.selector).kind !== 'string') throw new Error('Некорректный источник справки');
  docHref(rev,f.full);
  for(const key of ['related','equations','inputs','api','assumptions','limitations'])
    if(!Array.isArray(f[key])) throw new Error('Неполный фрагмент справки');
  for(const d of f.related as unknown[]) docHref(rev,d);
  for(const e of f.equations as unknown[]) {const row=object(e); if(typeof row.id !== 'string') throw new Error('Некорректное уравнение'); docHref(rev,row.doc);}
  for(const a of f.api as unknown[]) {const row=object(a); if(typeof row.symbol !== 'string') throw new Error('Некорректный API'); docHref(rev,row.doc);}
  for(const key of ['assumptions','limitations']) {
    const notes=f[key] as unknown[];
    if(notes.length > 12 || notes.some(note=>typeof note !== 'string' || note.length > 400)) throw new Error('Некорректные примечания');
  }
  return f as unknown as ReferenceFragment;
}
export async function checkedJson(url: string, maximum: number, expectedSha: string): Promise<unknown> {
  if(!/^\/docs\/[0-9a-f]{40}\/(?:bundle\.json|help\/fragments\/[a-z][a-z0-9._-]{0,127}\.json)(?![\s\S])/.test(url) ||
      !/^[0-9a-f]{64}(?![\s\S])/.test(expectedSha)) throw new Error('Некорректный адрес справки');
  const response=await fetch(url,{cache:'force-cache',redirect:'error'});
  if(!response.ok || !response.body) throw new Error('Справка недоступна');
  const reader=response.body.getReader(); const chunks:Uint8Array[]=[]; let size=0;
  try {
    while(true) {const {done,value}=await reader.read(); if(done) break; size+=value.byteLength;
      if(size>maximum) throw new Error('Справка превышает размер'); chunks.push(value);}
  } finally {await reader.cancel(); reader.releaseLock();}
  const raw=new Uint8Array(size); let offset=0; for(const chunk of chunks){raw.set(chunk,offset); offset+=chunk.length;}
  const hash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',raw)),b=>b.toString(16).padStart(2,'0')).join('');
  if(hash!==expectedSha) throw new Error('SHA-256 справки не совпадает');
  return JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(raw));
}
export function referenceIdentity(d: DocumentationAvailability | undefined): {revision: string; url: string; sha: string} | null {
  if(d?.status !== 'available' || !d.source_revision || !/^[0-9a-f]{40}(?![\s\S])/.test(d.source_revision) ||
     d.url!==`/docs/${d.source_revision}/` || !d.bundle_sha256 || !/^[0-9a-f]{64}(?![\s\S])/.test(d.bundle_sha256)) return null;
  return {revision:d.source_revision,url:d.url,sha:d.bundle_sha256};
}
