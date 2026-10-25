import assert from 'node:assert/strict';
import {test} from 'node:test';
import {api, configuredRequest, ordinarySubmission, submissionResources, type Config, type Resources} from '../src/api.ts';
import {SubmissionGate, mutationUncertain} from '../src/configurator-data.ts';

const manual:Resources={num_machines:1,num_mpiprocs_per_machine:1,num_cores_per_mpiproc:3,max_memory_kb:1024,max_wallclock_seconds:600};
const config:Config={defaults:manual,profile:'test',codes:['code'],exports:{disk_bytes:1,ttl_seconds:1},limits:{...manual,num_cores_per_mpiproc:12,max_memory_kb:34603008,plan_bytes:1024,artifact_bytes:1024},ordinary_resources:{request_selection:'configured_request_ceiling',allocation_authority:'slurm',availability:'not_measured',forecast:'advisory',runtime_overhead:'unknown',reserve:'from_frozen_runner_policy',confidence:'uncalibrated',allocated_cpus:'not_measured',allocated_memory:'not_measured'}};
test('manual fields survive; automatic sends no CPU/RAM overrides and displays configured request only',()=>{
 assert.deepEqual(submissionResources('manual',manual),manual);
 assert.notEqual(submissionResources('manual',manual),manual);
 assert.deepEqual(submissionResources('automatic',manual),{max_wallclock_seconds:600});
 assert.deepEqual(configuredRequest(config),{num_machines:1,num_mpiprocs_per_machine:1,num_cores_per_mpiproc:12,max_memory_kb:34603008});
 assert.equal(configuredRequest({...config,ordinary_resources:undefined}),null);
 assert.equal(configuredRequest({...config,limits:{...config.limits,max_memory_kb:Infinity}}),null);
 assert.equal(configuredRequest({...config,limits:{...config.limits,num_cores_per_mpiproc:1.5}}),null);
});
test('old config manual whole wire body has exactly the legacy fields',()=>{
 const oldConfig:Config={defaults:manual,profile:'legacy',codes:['code'],exports:{disk_bytes:1,ttl_seconds:1},limits:{...manual,plan_bytes:1024,artifact_bytes:1024}};
 const raw='{\n "energy": 1.234567890123456789\n}\n';
 const request=ordinarySubmission('manual',oldConfig,{plan:raw,code_uuid:'code',resources:manual,label:'Physics'});
 assert.deepEqual(request,{plan:raw,code_uuid:'code',resources:{num_machines:1,num_mpiprocs_per_machine:1,num_cores_per_mpiproc:3,max_memory_kb:1024,max_wallclock_seconds:600},label:'Physics'});
 assert.equal(Object.hasOwn(request,'resources_mode'),false);
});
test('automatic whole wire body requires capability and has explicit mode without CPU/RAM overrides',()=>{
 const draft={plan:'{}',code_uuid:'code',resources:manual,label:'Physics'};
 assert.deepEqual(ordinarySubmission('automatic',config,draft),{plan:'{}',code_uuid:'code',resources:{max_wallclock_seconds:600},label:'Physics',resources_mode:'automatic'});
 assert.throws(()=>ordinarySubmission('automatic',{...config,ordinary_resources:undefined},draft));
 assert.throws(()=>ordinarySubmission('automatic',null,draft));
});
test('one automatic POST preserves frozen raw plan and unknown outcome never retries',async()=>{
 const previous=globalThis.fetch;let calls=0;
 const raw='{\n "energy": 1.234567890123456789\n}\n';
 const gate=new SubmissionGate(),selected={plan:raw};gate.select(selected);
 globalThis.fetch=async(_url,init)=>{calls++;assert.deepEqual(JSON.parse(String(init?.body)),{plan:raw,code_uuid:'code',resources_mode:'automatic',resources:{max_wallclock_seconds:600},label:''});throw new TypeError('transport unknown');};
 try {
   const accepted=gate.begin();assert.equal(accepted,selected);
   let uncertain=false;
   await assert.rejects(async()=>{
     try {await api('secret','/runs',ordinarySubmission('automatic',config,{plan:accepted!.plan,code_uuid:'code',resources:manual,label:''}));}
     catch(error){uncertain=mutationUncertain(error);throw error;}
   },/transport unknown/);
   gate.finish(uncertain);
   gate.select({plan:raw});assert.equal(gate.begin(),null);assert.equal(calls,1);
 }finally{globalThis.fetch=previous;}
});
test('changed resources mode or Code invalidates an in-flight plan selection before dispatch',()=>{
 const gate=new SubmissionGate(),before={plan:'{}',sha256:'a'.repeat(64)};
 gate.select(before);assert.equal(gate.begin(),before);
 gate.select({...before});assert.equal(gate.current(before),false);
 gate.finish(false);gate.select(null);assert.equal(gate.begin(),null);
});
