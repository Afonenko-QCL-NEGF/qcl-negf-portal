import assert from 'node:assert/strict';
import {test} from 'node:test';
import {ApiError} from '../src/api.ts';
import {ClusterReader, parseCluster, mergeCluster, safeGraphUrl, formatClusterTime, MAX_CLUSTER_PAGES} from '../src/cluster-status-data.ts';

export function coverage(status='fresh') {return {source_id:'native:h',status,observed_at:10,host_boot_id:'boot',generation:'sample',complete:true,reasons:[]};}
export function page() {return {schema:'qcl.cluster-projection.v2',read_at:30,status:'ok',registry_generation:'registry',complete:false,next_cursor:'opaque+root/=',coverage:{sources:[coverage(),{...coverage(),source_id:'controller-cache:hash',observed_at:20}],reasons:[]},current_context:{schema:'qcl.current-context.v1',read_at:30,observed_at:10,cluster_id:'cluster',controller_boot_id:'boot',sample_generation:'context',status:'fresh',jobs:[{job_id:'12',owner_uid:0,state:'queued',native_state:'PENDING',reason:'Resources',node_expression:'node1',submit_time:0,start_time:null,restarts:0,cpus:0,requested_memory:{per_node_bytes:null,per_cpu_bytes:0},generation:'job',coverage:coverage()}],coverage:coverage(),complete:false,next_cursor:'inner-context'},hosts:[{host_id:'h',reasons:[{code:'owner_reason_new',source_id:'h'}],graph_links:[{label:'PVE',href:'https://example.invalid/graphs'}],services:[{service_id:'slurm',active_state:'failed',sub_state:'failed',result:'exit-code',invocation_id:'inv',observed_at:10,coverage:coverage('stale')}],storage:null,native:{schema:'qcl.native-status.v2',host_id:'h',host_boot_id:'boot',observed_at:10,sample_generation:'sample',host_memory:{total_bytes:0,available_bytes:null},vms:[{vm_id:'1',native_generation:'vm',qemu_pid:1,qemu_start_ticks:1,cpu_weight:100,power_state:'running',qemu_rss_bytes:null,assigned_bytes:0,configured_bytes:0,configured_max_bytes:10,configured_vcpus:1,online_vcpus:null,observed_at:10,coverage:coverage()}],oom:{events:[{event_id:'event-1',cause:'native_unit_kernel_oom',scope:'host',vm_id:null,workload:null,host_boot_id:'boot',guest_boot_id:null,event_at:10,source_id:'oom:h',native_generation:'sample',confidence:'observed',reasons:[],unit_id:'worker.service',unit_invocation_id:'a'.repeat(32),victim_kind:'unknown'}],coverage:{...coverage(),source_id:'oom:h',complete:false},next_cursor:'inner-oom'},coverage:coverage(),complete:false,next_cursor:'inner-native',capabilities:{kernel_qemu_victim_join:coverage(),systemd_manager_unit_oom:coverage()}},resource:{schema:'qcl.host-resource-projection.v1',observed_at:10,state_generation:'resource',status:'ok',host:null,vms:[{vm_id:'1',grant_state:'active',charged_bytes:0,desired_bytes:null,current_assigned_bytes:0,current_configured_bytes:10,protected_floor_bytes:0,boot_phase:'binding_ready',maintenance_required:false,failure_code:null,native_freshness:{status:'fresh',observed_at:10},cpu:{desired_weight:0,current_weight:0,configured_vcpus:1,online_vcpus:null,online_change_supported:false}}],operations:[{schema:'qcl.host-resource-operation.v1',request_id:'request',workload:{kind:'slurm_allocation',native_id:'12'},native_generation:'vm',host_id:'h',vm_id:'1',state_generation:'resource',phase:'RESOURCE_WAIT',admission:'resource_wait',failure_code:'OWNER_FAILURE',requested_workload_bytes:0,target_bytes:null,charged_bytes:0,current_assigned_bytes:0,current_configured_bytes:0,protected_floor_bytes:0,cleanup:'pending',native_freshness:{status:'stale',observed_at:10}}],complete:false,next_cursor:'inner-resource'}}]};}
function later<T>() {let resolve!:(v:T)=>void;let reject!:(e:Error)=>void;const promise=new Promise<T>((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};}
const settle=async()=>{for(let i=0;i<6;i++)await Promise.resolve();};
function clock() {let now=0,id=0;const tasks=new Map<number,{at:number,fn:()=>void}>();return {set:(fn:()=>void,ms:number)=>{tasks.set(++id,{at:now+ms,fn});return id;},clear:(key:unknown)=>{tasks.delete(key as number);},advance:(ms:number)=>{now+=ms;for(const [k,v] of tasks)if(v.at<=now){tasks.delete(k);v.fn();}},get count(){return tasks.size;}};}

test('DTO preserves owner null, real zero, reasons and separate timestamp events',()=>{
 const dto=parseCluster(page());assert.equal(dto.hosts[0].native?.host_memory.available_bytes,null);assert.equal(dto.hosts[0].native?.host_memory.total_bytes,0);assert.equal(dto.hosts[0].reasons[0].code,'owner_reason_new');assert.equal(dto.read_at,30);assert.equal(dto.coverage.sources[1].observed_at,20);assert.equal(dto.hosts[0].native?.observed_at,10);assert.match(formatClusterTime(0),/1970.*UTC/);assert.equal(formatClusterTime(null),'неизвестно');
});
test('unknown schema, malformed displayed fields and unsafe integers fail closed',()=>{
 for(const mutate of [(p:any)=>p.schema='future',(p:any)=>p.hosts[0].services[0].coverage.status='invented',(p:any)=>p.hosts[0].native.host_memory.total_bytes=Number.MAX_SAFE_INTEGER+1,(p:any)=>delete p.hosts[0].resource.operations[0].failure_code,(p:any)=>p.current_context.jobs[0].cpus='0']) {const p=page();mutate(p);assert.throws(()=>parseCluster(p),/cluster|projection|coverage|field|integer/i);}
});
test('graph links require credential-free HTTPS',()=>{for(const href of ['javascript:alert(1)','http://example.invalid','https://user:pass@example.invalid','//example.invalid','https://example.invalid/\n'])assert.equal(safeGraphUrl(href),null);assert.equal(safeGraphUrl('https://example.invalid/a'),'https://example.invalid/a');});
test('append uses registry and inner generations; duplicate resource rows merge without duplication',()=>{
 const a=parseCluster(page()), b=parseCluster(page());b.complete=true;b.next_cursor=null;b.hosts[0].resource!.complete=true;b.hosts[0].resource!.next_cursor=null;
 const merged=mergeCluster(null,a);const result=mergeCluster(merged,b);assert.equal(result.hosts.length,1);assert.equal(result.hosts[0].resource?.vms.length,1);assert.equal(result.hosts[0].resource?.operations.length,1);assert.equal(result.complete,true);
 for(const mutate of [(p:any)=>p.registry_generation='new',(p:any)=>p.hosts[0].native.sample_generation='new',(p:any)=>p.hosts[0].resource.state_generation='new',(p:any)=>p.current_context.sample_generation='new',(p:any)=>p.hosts[0].resource.vms[0].charged_bytes=99]) {const p=page();mutate(p);assert.throws(()=>mergeCluster(merged,parseCluster(p)),/drift/);}
});
test('bounded pages expose local truncation and never claim completeness',()=>{let result=mergeCluster(null,parseCluster(page()));for(let i=1;i<=MAX_CLUSTER_PAGES;i++)result=mergeCluster(result,parseCluster(page()));assert.equal(result.truncated,true);assert.equal(result.complete,false);assert.equal(result.next_cursor,null);assert.equal(result.pages,MAX_CLUSTER_PAGES);});
test('complete false with null root cursor cannot page inner cursors',async()=>{const p=page();p.next_cursor=null;const reader=new ClusterReader('token',()=>{},async()=>p);reader.activate(true);await settle();assert.equal(reader.state.snapshot?.complete,false);assert.equal(await reader.loadMore(),false);reader.dispose();});
test('one flight including manual paging; poll 30s after completion, hidden pauses and invalidates late data',async()=>{
 const timer=clock(), first=later<unknown>(), late=later<unknown>();const paths:string[]=[];let calls=0;
 const reader=new ClusterReader('secret',()=>{},async(_token,path)=>{paths.push(path);return ++calls===1?first.promise:late.promise;},timer);
 reader.activate(true);assert.equal(calls,1);assert.equal(await reader.refresh(),false);assert.equal(await reader.loadMore(),false);timer.advance(60000);assert.equal(calls,1);first.resolve(page());await settle();timer.advance(29999);assert.equal(calls,1);timer.advance(1);assert.equal(calls,2);assert.equal(paths[1],'/cluster/status?limit=64');reader.activate(false);late.resolve({...page(),read_at:999});await settle();assert.equal(reader.state.snapshot?.read_at,30);timer.advance(90000);assert.equal(calls,2);reader.dispose();
});
test('load more sends exact opaque root cursor and fresh refresh replaces pages',async()=>{const paths:string[]=[];const reader=new ClusterReader('secret',()=>{},async(_t,path)=>{paths.push(path);const p=page();if(paths.length===2){p.complete=true;p.next_cursor=null;}return p;},clock());reader.activate(true);await settle();await reader.loadMore();assert.equal(paths[1],'/cluster/status?limit=64&cursor=opaque%2Broot%2F%3D');assert.equal(reader.state.snapshot?.pages,2);await reader.refresh();assert.equal(reader.state.snapshot?.pages,1);reader.dispose();});
for(const status of [401,422,409,503])test(`HTTP ${status} stays explicit and never retries immediately`,async()=>{const timer=clock();let calls=0;const reader=new ClusterReader('token',()=>{},async()=>{calls++;throw new ApiError('refused',new Headers(),status);},timer);reader.activate(true);await settle();assert.match(reader.state.error!,new RegExp(String(status)));assert.equal(reader.state.drift,false);assert.equal(calls,1);timer.advance(29999);assert.equal(calls,1);reader.dispose();});
for(const source of ['409','mismatch'])test(`${source} drift drops all retained pages and requires explicit refresh`,async()=>{let calls=0;const timer=clock();const reader=new ClusterReader('token',()=>{},async()=>{if(++calls===2){if(source==='409')throw new ApiError('refused',new Headers(),409,'stale_cursor');const p=page();p.registry_generation='new';return p;}return page();},timer);reader.activate(true);await settle();await reader.loadMore();assert.equal(reader.state.snapshot,null);assert.equal(reader.state.drift,true);timer.advance(90000);assert.equal(calls,2);await reader.refresh();assert.ok(reader.state.snapshot);reader.dispose();});
for(const mode of ['dispose','hidden'])test(`${mode} invalidates late response/error and aborts request`,async()=>{const pending=later<unknown>();let signal:AbortSignal|undefined;const reader=new ClusterReader('token',()=>{},async(_t,_p,s)=>{signal=s;return pending.promise;},clock());reader.activate(true);if(mode==='dispose')reader.dispose();else reader.activate(false);assert.equal(signal?.aborted,true);pending.reject(new Error('late'));await settle();assert.equal(reader.state.error,null);assert.equal(reader.state.snapshot,null);});
test('visibility resume waits for pending cancelled request then refreshes exactly once',async()=>{const pending=later<unknown>();let calls=0;const reader=new ClusterReader('token',()=>{},async()=>++calls===1?pending.promise:page(),clock());reader.activate(true);reader.activate(false);reader.activate(true);assert.equal(calls,1);pending.resolve(page());await settle();assert.equal(calls,2);assert.ok(reader.state.snapshot);reader.dispose();});
test('network failure retains last success explicitly with error; new poll replaces snapshot',async()=>{let calls=0;const reader=new ClusterReader('token',()=>{},async()=>{if(++calls===2)throw new Error('offline');return page();},clock());reader.activate(true);await settle();await reader.refresh();assert.equal(reader.state.snapshot?.read_at,30);assert.match(reader.state.error!,/unavailable.*offline/);reader.dispose();});

test('row bound drops an oversized first page explicitly and bounds appended state',()=>{
 const raw=page();raw.hosts=[];
 for(let i=0;i<128;i++){const h=structuredClone(page().hosts[0]);h.host_id=`h${i}`;h.native.host_id=h.host_id;h.services=Array.from({length:20},(_,n)=>({...h.services[0],service_id:`s${n}`}));raw.hosts.push(h);}
 const snapshot=mergeCluster(null,parseCluster(raw));assert.equal(snapshot.truncated,true);assert.equal(snapshot.complete,false);assert.equal(snapshot.next_cursor,null);assert.equal(snapshot.hosts.length,0);
});
test('abort signal reaches actual fetch and no-store auth survives cancellation',async()=>{
 const previous=globalThis.fetch,entered=later<void>();let signal:AbortSignal|undefined;
 globalThis.fetch=async(_path,init)=>{signal=init?.signal as AbortSignal;assert.equal(init?.cache,'no-store');assert.equal((init?.headers as Record<string,string>).Authorization,'Bearer secret');entered.resolve();return new Promise<Response>((_ok,fail)=>{signal!.addEventListener('abort',()=>fail(new Error('cancelled')));});};
 const reader=new ClusterReader('secret',()=>{});
 try {reader.activate(true);await entered.promise;reader.dispose();await settle();assert.equal(signal?.aborted,true);assert.equal(reader.state.snapshot,null);assert.equal(reader.state.error,null);} finally {reader.dispose();globalThis.fetch=previous;}
});


test('actual owner OOM shape accepts events/coverage and preserves reasons, time and event identity',()=>{
 const p=page();const result=parseCluster(p);assert.equal(result.hosts[0].native?.oom.coverage.complete,false);assert.equal(result.hosts[0].native?.oom.events[0].event_id,'event-1');
 const old=structuredClone(p) as any;delete old.hosts[0].native.oom.coverage;old.hosts[0].native.oom.complete=true;assert.throws(()=>parseCluster(old));
});
for(const mutate of [(p:any)=>p.hosts[0].native.host_memory.total_bytes=200,(p:any)=>p.hosts[0].native.vms[0].qemu_pid=999,(p:any)=>p.hosts[0].native.oom.events[0].confidence='unknown',(p:any)=>p.hosts[0].services[0].active_state='active',(p:any)=>p.hosts[0].services[0].invocation_id='another',(p:any)=>p.hosts[0].storage.used_bytes=99])test('same generation conflicting host content rejects without a mixed snapshot',()=>{
 const a=page() as any;a.hosts[0].storage={schema:'qcl.telemetry-storage.v2',status:'qualified',binding_kind:'dedicated_mount',maximum_bytes:100,used_bytes:0,available_bytes:100,keep_free_bytes:0,inodes_available:100,retention_seconds:3600,observed_at:10,reasons:[],persist_permitted:true,allocation_bytes:100,allocated_reserve_bytes:0,science_charge_bytes:0,capacity_generation:'storage',collector_state:'active'};const b=structuredClone(a);mutate(b);
 assert.throws(()=>mergeCluster(mergeCluster(null,parseCluster(a)),parseCluster(b)),/drift/);
});
test('compatible resource continuation accepts only owner freshness normalization and keeps older operations',()=>{
 const a=page() as any;a.hosts[0].services[0].coverage.status='fresh';a.hosts[0].storage={schema:'qcl.telemetry-storage.v2',status:'qualified',binding_kind:'dedicated_mount',maximum_bytes:100,used_bytes:0,available_bytes:100,keep_free_bytes:0,inodes_available:100,retention_seconds:3600,observed_at:10,reasons:[],persist_permitted:true,allocation_bytes:100,allocated_reserve_bytes:0,science_charge_bytes:0,capacity_generation:'storage',collector_state:'active'};const b=structuredClone(a);
 b.read_at=100;b.complete=true;b.next_cursor=null;b.hosts[0].resource.complete=true;b.hosts[0].resource.next_cursor=null;b.hosts[0].resource.observed_at=100;b.hosts[0].resource.operations=[];
 const stale=(c:any)=>{c.status='stale';c.complete=false;c.reasons.push({code:'sample_stale',source_id:c.source_id});};
 stale(b.hosts[0].native.coverage);stale(b.hosts[0].native.vms[0].coverage);stale(b.hosts[0].native.oom.coverage);stale(b.hosts[0].native.capabilities.kernel_qemu_victim_join);stale(b.hosts[0].services[0].coverage);
 b.hosts[0].storage.status='unknown';b.hosts[0].storage.persist_permitted=false;b.hosts[0].storage.reasons=[{code:'storage_stale',source_id:'telemetry'}];
 const merged=mergeCluster(mergeCluster(null,parseCluster(a)),parseCluster(b));assert.equal(merged.hosts.length,1);assert.equal(merged.hosts[0].resource?.operations.length,1);assert.equal(merged.hosts[0].native?.coverage.status,'stale');assert.equal(merged.hosts[0].storage?.status,'unknown');assert.equal(merged.complete,true);
});
test('a duplicate host conflict is exposed as drift and clears every retained page',async()=>{
 let calls=0;const reader=new ClusterReader('token',()=>{},async()=>{const p=page();if(++calls===2)p.hosts[0].native.host_memory.total_bytes=100;return p;},clock());reader.activate(true);await settle();await reader.loadMore();assert.equal(reader.state.snapshot,null);assert.equal(reader.state.drift,true);reader.dispose();
});


test('failed refresh remains unavailable during pending retry until a new successful sample',async()=>{
 let calls=0;const pending=later<unknown>();const reader=new ClusterReader('token',()=>{},async()=>{if(++calls===2)throw new Error('offline');if(calls===3)return pending.promise;return page();},clock());
 reader.activate(true);await settle();await reader.refresh();const retry=reader.refresh();assert.match(reader.state.error!,/offline/);assert.equal(reader.state.busy,true);pending.resolve(page());await retry;assert.equal(reader.state.error,null);reader.dispose();
});
test('arbitrary coverage changes cannot masquerade as per-read freshness normalization',()=>{
 const a=page(),b=page();b.hosts[0].native.coverage.status='stale';b.hosts[0].native.coverage.complete=false;
 assert.throws(()=>mergeCluster(mergeCluster(null,parseCluster(a)),parseCluster(b)),/drift/);
 b.hosts[0].native.coverage.reasons=[{code:'unknown_new_reason',source_id:'native:h'}];assert.throws(()=>mergeCluster(mergeCluster(null,parseCluster(a)),parseCluster(b)),/drift/);
});
test('equivalent object property order is compatible and context freshness demotion does not corrupt paging',()=>{
 const a=page(),b=page();b.hosts[0].native.coverage=Object.fromEntries(Object.entries(b.hosts[0].native.coverage).reverse()) as ReturnType<typeof coverage>;
 assert.doesNotThrow(()=>mergeCluster(mergeCluster(null,parseCluster(a)),parseCluster(b)));
 const stale=(c:any)=>{c.status='stale';c.complete=false;c.reasons.push({code:'sample_stale',source_id:c.source_id});};
 b.current_context.status='stale';b.current_context.complete=false;stale(b.current_context.coverage);stale(b.current_context.jobs[0].coverage);
 assert.doesNotThrow(()=>mergeCluster(mergeCluster(null,parseCluster(a)),parseCluster(b)));
});

// Literal public-normalizer rules; the separately retained reviewer controls use
// the actual Python owner functions and full schemas as the independent oracle.
function normalizePageAt(raw: ReturnType<typeof page>, now: number) {
 const p=structuredClone(raw) as any;
 const cov=(c:any,at:number|null)=>{
  let code:string|null=null;
  if([at,c.observed_at].some(t=>t!==null&&t>now)){c.status='inconsistent';code='clock_future';}
  else if([at,c.observed_at].some(t=>t===null)){if(['fresh','stale'].includes(c.status)){c.status='not_measured';code='sample_not_measured';}}
  else if([at,c.observed_at].some(t=>now-t>60)){if(['fresh','stale'].includes(c.status)){c.status='stale';code='sample_stale';}}
  if(code){c.complete=false;const r={code,source_id:c.source_id};if(!c.reasons.some((v:any)=>v.code===code&&v.source_id===c.source_id))c.reasons=[...c.reasons.slice(0,127),r];}
 };
 p.read_at=now;
 for(const h of p.hosts){const n=h.native;if(n){cov(n.coverage,n.observed_at);for(const vm of n.vms)cov(vm.coverage,vm.observed_at);for(const c of Object.values(n.capabilities) as any[])cov(c,c.observed_at);cov(n.oom.coverage,n.oom.coverage.observed_at);}for(const s of h.services)cov(s.coverage,s.observed_at);if(h.storage){const s=h.storage,at=s.observed_at;if(at===null||at>now||now-at>60){s.status='unknown';s.persist_permitted=false;const code=at!==null&&at>now?'clock_future':'storage_stale';if(!s.reasons.some((r:any)=>r.code===code&&r.source_id==='telemetry'))s.reasons=[...s.reasons.slice(0,127),{code,source_id:'telemetry'}];}}}
 for(const c of p.coverage.sources)cov(c,c.observed_at);
 if(p.current_context){const c=p.current_context;c.read_at=now;if(c.observed_at!==null){if(c.observed_at>now){c.status='unknown';c.complete=false;}else if(now-c.observed_at>60){c.status='stale';c.complete=false;}}cov(c.coverage,c.observed_at);for(const job of c.jobs)cov(job.coverage,c.observed_at);}
 return p;
}
function paired(raw=page(),a=30,b=100) {return [normalizePageAt(raw,a),normalizePageAt(raw,b)] as const;}
function appendPair(a:unknown,b:unknown) {return mergeCluster(mergeCluster(null,parseCluster(a)),parseCluster(b));}
for(const length of [127,128])test(`owner reason list ${length} accepts exact bounded normalization append`,()=>{
 const raw=page();raw.hosts[0].native.coverage.reasons=Array.from({length},(_,i)=>({code:`owner_${i}`,source_id:'native:h'})) as any;
 const [a,b]=paired(raw);assert.equal(b.hosts[0].native.coverage.reasons.length,128);assert.doesNotThrow(()=>appendPair(a,b));
 const bad=structuredClone(b);bad.hosts[0].native.coverage.reasons[0].code='changed_owner';assert.throws(()=>appendPair(a,bad),/drift/);
});
test('same cache clock_future recovers original fresh complete and context without losing identity',()=>{
 const [a,b]=paired(page(),9,30);assert.equal(a.current_context.status,'unknown');assert.equal(b.current_context.status,'fresh');assert.doesNotThrow(()=>appendPair(a,b));
 for(const mutate of [(p:any)=>p.hosts[0].native.host_memory.total_bytes=999,(p:any)=>p.hosts[0].native.coverage.observed_at=11,(p:any)=>p.current_context.controller_boot_id='another',(p:any)=>p.hosts[0].services[0].invocation_id='another']){const bad=structuredClone(b);mutate(bad);assert.throws(()=>appendPair(a,bad),/drift/);}
});
test('two different normalizations of a saturated list share only the exact retained owner prefix',()=>{
 const raw=page();raw.hosts[0].native.coverage.reasons=Array.from({length:128},(_,i)=>({code:`owner_${i}`,source_id:'native:h'})) as any;
 const [a,b]=paired(raw,9,100);assert.doesNotThrow(()=>appendPair(a,b));const bad=structuredClone(b);bad.hosts[0].native.coverage.reasons[126].code='changed';assert.throws(()=>appendPair(a,bad),/drift/);
});
test('ordinary and temporal existing reason codes never authorize arbitrary reason or completeness loss',()=>{
 const [a,b]=paired();b.hosts[0].native.coverage.reasons.push({code:'invented',source_id:'native:h'});assert.throws(()=>appendPair(a,b),/drift/);
 const c=normalizePageAt(page(),30),d=structuredClone(c);d.hosts[0].native.coverage.complete=false;assert.throws(()=>appendPair(c,d),/drift/);
});

test('future coverage recovers to not_measured when the entity measurement stays null',()=>{
 const raw=page();raw.hosts[0].native.observed_at=null as any;const [a,b]=paired(raw,9,30);assert.equal(a.hosts[0].native.coverage.status,'inconsistent');assert.equal(b.hosts[0].native.coverage.status,'not_measured');assert.doesNotThrow(()=>appendPair(a,b));
});
test('saturated storage reasons and temporary future status recover only the immutable storage content',()=>{
 const raw=page() as any;raw.hosts[0].storage={schema:'qcl.telemetry-storage.v2',status:'qualified',observed_at:10,used_bytes:0,available_bytes:100,persist_permitted:true,reasons:Array.from({length:128},(_,i)=>({code:`storage_owner_${i}`,source_id:'telemetry'}))};
 for(const times of [[30,100],[9,30],[9,100]]){const [a,b]=paired(raw,...times as [number,number]);assert.doesNotThrow(()=>appendPair(a,b));const bad=structuredClone(b);bad.hosts[0].storage.available_bytes=99;assert.throws(()=>appendPair(a,bad),/drift/);}
});
