import assert from 'node:assert/strict';
import {test} from 'node:test';
import {readFile} from 'node:fs/promises';
import {inclusionTree, pointPage, readPlanStructure} from '../src/run-structure-data.ts';
import type {AvailableStructure} from '../src/run-structure-data.ts';

function fixture(): AvailableStructure {
  return {status:'available',root:{kind:'meta',definition_id:'root'},
    inclusions:[{id:'i0',parent_id:null,definition_id:'root',label:'Root',path:['root'],execution_ids:['e1','e2']},
      {id:'i1',parent_id:'i0',definition_id:'d1',label:'Shared',path:['root','d1'],execution_ids:['e1']},
      {id:'i2',parent_id:'i0',definition_id:'d1',label:'Shared again',path:['root','d1'],execution_ids:['e1']}],
    executions:[{id:'e1',definition_id:'d1',variant_id:'v1',label:'First',operation:'transport',point_ids:['p1','p2'],voltage_mode:'continuation'},
      {id:'e2',definition_id:'d2',variant_id:'v2',label:'Second',operation:'transport',point_ids:['p3'],voltage_mode:'independent'}],
    points:[{id:'p1',execution_id:'e1',branch:'T1',order:1,initialization:'cold',predecessor_id:null,temperature_K:70,voltage_per_period_V:0},
      {id:'p2',execution_id:'e1',branch:'T1',order:2,initialization:'previous',predecessor_id:'p1',temperature_K:70,voltage_per_period_V:0.05},
      {id:'p3',execution_id:'e2',branch:'T1',order:3,initialization:'cold',predecessor_id:null,temperature_K:70,voltage_per_period_V:0.1}]};
}
test('literal explicit edge and independent execution, shared inclusion memberships',()=>{
  const value=fixture(); assert.deepEqual(readPlanStructure(value),value);
  assert.deepEqual(value.points.map(p=>[p.predecessor_id,p.id]),[[null,'p1'],['p1','p2'],[null,'p3']]);
  assert.deepEqual(inclusionTree(value.inclusions).map(x=>[x.row.id,x.depth]),[['i0',0],['i1',1],['i2',1]]);
});
test('old producer, explicit unavailable and malformed payloads have no invented defaults',()=>{
  for(const value of [undefined,null,{},[],{status:'available'}, {status:'unavailable',reason:'invented'},{status:'unavailable',reason:{toString:'invalid'}}]) assert.equal(readPlanStructure(value),null);
  for(const reason of ['unsupported_plan_schema','unsupported_structure'])assert.deepEqual(readPlanStructure({status:'unavailable',reason}),{status:'unavailable',reason});
});
for(const fault of ['duplicate','foreign','later','cycle','dangling','membership','bool','nan','infinity','mode','extra','duplicate_ref','temperature','branch']) test(`malformed ${fault} unavailable`,()=>{
  const value=fixture();
  switch(fault){
    case 'duplicate':value.points[1].id='p1';break;
    case 'foreign':value.points[2].predecessor_id='p2';break;
    case 'later':value.points[0].predecessor_id='p2';break;
    case 'cycle':value.inclusions[0].parent_id='i1';break;
    case 'dangling':value.inclusions[0].execution_ids.push('absent');break;
    case 'membership':value.executions[0].point_ids=['p1'];break;
    case 'bool':Object.assign(value.points[0],{order:true});break;
    case 'nan':value.points[0].temperature_K=NaN;break;
    case 'infinity':value.points[0].voltage_per_period_V=Infinity;break;
    case 'mode':Object.assign(value.executions[0],{voltage_mode:null});break;
    case 'extra':Object.assign(value.points[0],{effective_inputs:{}});break;
    case 'duplicate_ref':value.inclusions[0].execution_ids.push('e1');break;
    case 'temperature':value.points[1].temperature_K=71;break;
    case 'branch':value.points[1].branch='other';break;
  }
  assert.equal(readPlanStructure(value),null);
});
test('101 rows are reachable with total and no discarded point',()=>{
  const points=Array.from({length:101},(_,i)=>({...fixture().points[0],id:`point-${i}`,order:i+1}));
  assert.equal(pointPage(points,0).rows.length,100);assert.equal(pointPage(points,0).total,101);
  assert.deepEqual(pointPage(points,1).rows.map(p=>p.id),['point-100']);
  assert.deepEqual([...pointPage(points,0).rows,...pointPage(points,1).rows],points);
});
test('run switch retains refresh sequence guard and mounts keyed by exact detail UUID',async()=>{
  const source=await readFile(new URL('../src/main.tsx',import.meta.url),'utf8');
  assert.match(source,/<RunStructure key=\{detail.uuid\} run=\{detail\}/);
  assert.match(source,/refreshSequence/);assert.match(source,/sequence !== refreshSequence.current/);
});
