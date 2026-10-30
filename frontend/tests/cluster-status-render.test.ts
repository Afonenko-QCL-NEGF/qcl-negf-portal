import assert from 'node:assert/strict';
import {test} from 'node:test';
import {readFile} from 'node:fs/promises';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import ts from 'typescript';
function coverage(status='fresh') {return {source_id:'native:h',status,observed_at:10,host_boot_id:'boot',generation:'sample',complete:true,reasons:[]};}
function page() {return {schema:'qcl.cluster-projection.v2',read_at:30,status:'ok',registry_generation:'registry',complete:false,next_cursor:'opaque+root/=',coverage:{sources:[coverage(),{...coverage(),source_id:'controller-cache:hash',observed_at:20}],reasons:[]},current_context:{schema:'qcl.current-context.v1',read_at:30,observed_at:10,cluster_id:'cluster',controller_boot_id:'boot',sample_generation:'context',status:'fresh',jobs:[{job_id:'12',owner_uid:0,state:'queued',native_state:'PENDING',reason:'Resources',node_expression:'node1',submit_time:0,start_time:null,restarts:0,cpus:0,requested_memory:{per_node_bytes:null,per_cpu_bytes:0},generation:'job',coverage:coverage()}],coverage:coverage(),complete:false,next_cursor:'inner-context'},hosts:[{host_id:'h',reasons:[{code:'owner_reason_new',source_id:'h'}],graph_links:[{label:'PVE',href:'https://example.invalid/graphs'}],services:[{service_id:'slurm',active_state:'failed',sub_state:'failed',result:'exit-code',invocation_id:'inv',observed_at:10,coverage:coverage('stale')}],storage:null,native:{schema:'qcl.native-status.v2',host_id:'h',host_boot_id:'boot',observed_at:10,sample_generation:'sample',host_memory:{total_bytes:0,available_bytes:null},vms:[{vm_id:'1',native_generation:'vm',qemu_pid:1,qemu_start_ticks:1,cpu_weight:100,power_state:'running',qemu_rss_bytes:null,assigned_bytes:0,configured_bytes:0,configured_max_bytes:10,configured_vcpus:1,online_vcpus:null,observed_at:10,coverage:coverage()}],oom:{events:[{event_id:'event-1',cause:'native_unit_kernel_oom',scope:'host',vm_id:null,workload:null,host_boot_id:'boot',guest_boot_id:null,event_at:10,source_id:'oom:h',native_generation:'sample',confidence:'observed',reasons:[],unit_id:'worker.service',unit_invocation_id:'a'.repeat(32),victim_kind:'unknown'}],coverage:{...coverage(),source_id:'oom:h',complete:false},next_cursor:'inner-oom'},coverage:coverage(),complete:false,next_cursor:'inner-native',capabilities:{kernel_qemu_victim_join:coverage(),systemd_manager_unit_oom:coverage()}},resource:{schema:'qcl.host-resource-projection.v1',observed_at:10,state_generation:'resource',status:'ok',host:null,vms:[{vm_id:'1',grant_state:'active',charged_bytes:0,desired_bytes:null,current_assigned_bytes:0,current_configured_bytes:10,protected_floor_bytes:0,boot_phase:'binding_ready',maintenance_required:false,failure_code:null,native_freshness:{status:'fresh',observed_at:10},cpu:{desired_weight:0,current_weight:0,configured_vcpus:1,online_vcpus:null,online_change_supported:false}}],operations:[{schema:'qcl.host-resource-operation.v1',request_id:'request',workload:{kind:'slurm_allocation',native_id:'12'},native_generation:'vm',host_id:'h',vm_id:'1',state_generation:'resource',phase:'RESOURCE_WAIT',admission:'resource_wait',failure_code:'OWNER_FAILURE',requested_workload_bytes:0,target_bytes:null,charged_bytes:0,current_assigned_bytes:0,current_configured_bytes:0,protected_floor_bytes:0,cleanup:'pending',native_freshness:{status:'stale',observed_at:10}}],complete:false,next_cursor:'inner-resource'}}]};}
import {parseCluster,mergeCluster} from '../src/cluster-status-data.ts';
const filename=new URL('../src/cluster-status.tsx',import.meta.url);
const source=await readFile(filename,'utf8');
let output=ts.transpileModule(source,{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText;
for(const specifier of ['react','react/jsx-runtime','./api.ts','./cluster-status-data.ts']) {
 const resolved=specifier.startsWith('.')?new URL(specifier,filename).href:import.meta.resolve(specifier);
 output=output.replaceAll(`"${specifier}"`,JSON.stringify(resolved)).replaceAll(`'${specifier}'`,JSON.stringify(resolved));
}
const ui=await import(`data:text/javascript;base64,${Buffer.from(output).toString('base64')}`);
const render=(state:unknown)=>renderToStaticMarkup(createElement(ui.ClusterStatusView,{state,onRefresh:()=>{},onMore:()=>{}}));
test('cluster screen starts collapsed and authenticated; static render makes no request',()=>{
 const html=renderToStaticMarkup(createElement(ui.ClusterStatus,{token:'secret'}));
 assert.match(html,/Состояние кластера/);assert.doesNotMatch(html,/open=""|secret|0 B/);
});
test('snapshot shows scoped owner coverage, grants, jobs, failure, timestamps and safe PVE links',()=>{
 const p=page();p.hosts[0].graph_links.push({label:'unsafe',href:'javascript:alert(1)'});p.hosts[0].reasons[0].code='<script>reason</script>';
 const html=render({snapshot:mergeCluster(null,parseCluster(p)),busy:false,error:null,drift:false});
 for(const literal of ['partial','stale','RESOURCE_WAIT','resource_wait','OWNER_FAILURE','Resources','PENDING','exit-code','controller-cache:hash','Получение cache source','Чтение projection','Измерение native','0 B','неизвестно','1970-01-01T00:00:10.000Z','1970-01-01T00:00:20.000Z','1970-01-01T00:00:30.000Z','UTC','Загрузить ещё','неполный','&lt;script&gt;reason&lt;/script&gt;'])assert.ok(html.includes(literal),literal);
 assert.match(html,/href="https:\/\/example.invalid\/graphs"/);assert.match(html,/rel="noopener noreferrer"/);assert.doesNotMatch(html,/javascript:|<script>|href="inner-|secret/);
});
test('absence and incomplete null cursor are explicit and have no paging button',()=>{
 const p=page();p.next_cursor=null;p.hosts[0].native=null;p.hosts[0].resource=null;p.hosts[0].services=[];p.hosts[0].graph_links=[];p.current_context=null;
 const html=render({snapshot:mergeCluster(null,parseCluster(p)),busy:false,error:null,drift:false});
 for(const literal of ['native: неизвестно','resource: неизвестно','Графики не предоставлены','current_context: неизвестно','Продолжение недоступно'])assert.ok(html.includes(literal),literal);
 assert.doesNotMatch(html,/Загрузить ещё|0 B/);
});
test('loading/errors/drift/local truncation remain semantic, with old success clearly labelled',()=>{
 assert.match(render({snapshot:null,busy:true,error:null,drift:false}),/Загрузка/);
 for(const error of ['HTTP 401','HTTP 422','HTTP 503','unavailable: offline']) {
 const html=render({snapshot:mergeCluster(null,parseCluster(page())),busy:false,error,drift:false});assert.ok(html.includes(error));assert.match(html,/Последний успешный снимок/);assert.match(html,/Текущая доступность неизвестна/);assert.match(html,/role="alert"/);
 }
 const snapshot={...mergeCluster(null,parseCluster(page())),truncated:true,complete:false,next_cursor:null};assert.match(render({snapshot,busy:false,error:null,drift:false}),/Локальное усечение/);
 assert.match(render({snapshot:null,busy:false,error:'Snapshot drift',drift:true}),/страницы отброшены/);
});
test('fixed light theme, focus outline, and escaped text remain explicit in source',async()=>{
 const css=await readFile(new URL('../src/style.css',import.meta.url),'utf8');assert.match(css,/color-scheme:\s*light/);assert.doesNotMatch(css,/prefers-color-scheme:\s*dark/);assert.match(css,/\.cluster-panel[\s\S]*background:\s*#fff/);assert.match(css,/\.cluster-panel[\s\S]*focus-visible[\s\S]*outline/);assert.doesNotMatch(source,/dangerouslySetInnerHTML/);
 const main=await readFile(new URL('../src/main.tsx',import.meta.url),'utf8');assert.match(main,/token && <ClusterStatus key=\{`cluster:\$\{token\}`\} token=\{token\}/);
});

test('actual label requires all displayed resource freshness and sources; stale operation cannot become actual',()=>{
 const p=page();p.complete=true;p.next_cursor=null;p.hosts[0].reasons=[];p.hosts[0].native!.complete=true;p.hosts[0].native!.oom.coverage.complete=true;p.hosts[0].services[0].coverage=coverage();p.hosts[0].resource!.complete=true;p.current_context.complete=true;
 let html=render({snapshot:mergeCluster(null,parseCluster(p)),busy:false,error:null,drift:false});assert.doesNotMatch(html,/actual \(coverage fresh\)/);assert.match(html,/stale \/ partial/);
 p.hosts[0].native!.host_memory.available_bytes=0;p.hosts[0].native!.vms[0].qemu_rss_bytes=0;p.hosts[0].native!.vms[0].online_vcpus=0;
 p.hosts[0].resource!.operations[0].native_freshness.status='fresh';p.hosts[0].storage={schema:'qcl.telemetry-storage.v2',status:'qualified',binding_kind:'dedicated_mount',maximum_bytes:100,used_bytes:0,available_bytes:0,keep_free_bytes:0,inodes_available:100,retention_seconds:3600,observed_at:10,reasons:[],persist_permitted:true,allocation_bytes:100,allocated_reserve_bytes:0,science_charge_bytes:0,capacity_generation:'storage',collector_state:'active'} as any;
 html=render({snapshot:mergeCluster(null,parseCluster(p)),busy:false,error:null,drift:false});assert.match(html,/actual \(coverage fresh\)/);
 p.hosts[0].resource!.operations[0].native_freshness.status='not_measured';html=render({snapshot:mergeCluster(null,parseCluster(p)),busy:false,error:null,drift:false});assert.match(html,/partial/);assert.doesNotMatch(html,/actual \(coverage fresh\)/);
});

for(const status of ['not_measured','inconsistent','unsupported'])test(`owner ${status} coverage is visible and cannot become actual`,()=>{
 const p=page();p.hosts[0].native!.coverage.status=status;p.hosts[0].native!.coverage.reasons=[{code:'new_owner_reason',source_id:'native:h'}] as any;
 const html=render({snapshot:mergeCluster(null,parseCluster(p)),busy:false,error:null,drift:false});assert.ok(html.includes(status));assert.ok(html.includes('new_owner_reason'));assert.doesNotMatch(html,/actual \(coverage fresh\)/);
});
test('owner unknown and no numeric measurements stay unknown, with no invented zeros',()=>{
 const p=page();p.status='unknown';p.hosts=[];p.coverage.sources=[];p.current_context=null;
 const html=render({snapshot:mergeCluster(null,parseCluster(p)),busy:false,error:null,drift:false});assert.match(html,/Состояние: <strong>unknown/);assert.doesNotMatch(html,/0 B|actual/);
});


test('failed refresh is currently unavailable and cached coverage remains explicitly historical',()=>{
 const p=page();p.complete=true;p.next_cursor=null;p.hosts[0].reasons=[];p.hosts[0].native.complete=true;p.hosts[0].native.oom.coverage.complete=true;p.current_context.complete=true;p.hosts[0].resource.complete=true;p.hosts[0].resource.operations=[];p.hosts[0].native.host_memory.available_bytes=0;p.hosts[0].native.vms[0].qemu_rss_bytes=0;p.hosts[0].native.vms[0].online_vcpus=0;p.hosts[0].services[0].coverage=coverage();p.hosts[0].storage={schema:'qcl.telemetry-storage.v2',status:'qualified',binding_kind:'dedicated_mount',maximum_bytes:100,used_bytes:0,available_bytes:0,keep_free_bytes:0,inodes_available:100,retention_seconds:3600,observed_at:10,reasons:[],persist_permitted:true,allocation_bytes:100,allocated_reserve_bytes:0,science_charge_bytes:0,capacity_generation:'storage',collector_state:'active'} as any;
 const snapshot=mergeCluster(null,parseCluster(p));assert.match(render({snapshot,busy:false,error:null,drift:false}),/actual \(coverage fresh\)/);
 const html=render({snapshot,busy:false,error:'unavailable: offline',drift:false});assert.match(html,/Текущее состояние: <strong>unavailable/);assert.match(html,/Последний успешный снимок/);assert.doesNotMatch(html,/Состояние: <strong>actual/);assert.match(html,/Историческая coverage/);
});
test('owner OOM event, coverage status, source time and reasons are rendered',()=>{
 const p=page();p.hosts[0].native.oom.coverage.status='not_measured';p.hosts[0].native.oom.coverage.reasons=[{code:'journal_gap',source_id:'oom:h'}];
 const html=render({snapshot:mergeCluster(null,parseCluster(p)),busy:false,error:null,drift:false});for(const text of ['native_unit_kernel_oom','event-1','oom:h','journal_gap','not_measured','worker.service','1970-01-01T00:00:10.000Z'])assert.ok(html.includes(text),text);
});
