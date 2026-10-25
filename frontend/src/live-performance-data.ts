export type RecordValue=Record<string,unknown>;
export const object=(v:unknown):v is RecordValue=>v!==null&&typeof v==='object'&&!Array.isArray(v);
export function checkedLive(value:unknown,exact:RecordValue,kind:string):RecordValue{
 if(!object(value)||!object(value.provider_selection))throw new Error('Exact provider identity required');
 const provider=value.provider_selection;
 if(Object.entries(exact).some(([k,v])=>provider[k]!==v))throw new Error('Exact provider identity mismatch');
 if(kind==='performance'){
  if(value.status==='unavailable'){if(typeof value.reason!=='string')throw new Error('Missing unavailable reason');return value;}
  if(value.schema!=='qcl-negf.performance-summary-view.v1'||value.scope!=='execution_attempt'||value.closed!==true||!object(value.summary)||!object(value.binding)||!object(value.binding.selection))throw new Error('Exact closed performance summary required');
  const native=value.binding.selection;
  if(Object.entries(exact).filter(([k])=>k!=='run_uuid').some(([k,v])=>native[k]!==v))throw new Error('Performance owner mismatch');
 }else if(value.scope!=='closed_prefix'||value.descriptor_verified!==true||typeof value.index_sha256!=='string'||!Number.isSafeInteger(value.generation)||!object(value.cutoff))throw new Error('Verified closed prefix required');
 return value;
}
