import {useEffect, useRef, useState} from 'react';
import {errorMessage} from './api.ts';
import {getRawReport, postReport, prepareReport, ReportGate, type ReportPreview} from './agent-report-data.ts';

export function AgentReportUpload({token, uuid, gate, refresh}:{token:string;uuid:string;gate:ReportGate;refresh:()=>void}) {
  const [preview,setPreview]=useState<ReportPreview|null>(null);
  const [reviewed,setReviewed]=useState(false);
  const [message,setMessage]=useState('');
  const [rawRead,setRawRead]=useState('');
  const [,update]=useState(0);
  const generation=useRef(0);
  const reviewedRef=useRef(false);
  const identity=useRef({token,uuid});
  if (identity.current.token !== token || identity.current.uuid !== uuid) {
    identity.current={token,uuid}; generation.current++; reviewedRef.current=false;
  }
  useEffect(()=>gate.subscribe(()=>update(n=>n+1)),[gate]);
  useEffect(()=>{
    setPreview(null); setReviewed(false); setRawRead(''); setMessage('');
    return ()=>{generation.current++;};
  },[token,uuid]);
  async function choose(file:File|undefined) {
    const current=++generation.current;
    reviewedRef.current=false; setPreview(null);setReviewed(false);setRawRead('');setMessage('');
    if (!file) return;
    try {const value=await prepareReport(file,uuid);if(current===generation.current)setPreview(value);}
    catch(error){if(current===generation.current)setMessage(errorMessage(error));}
  }
  async function submit() {
    if (!preview || !reviewedRef.current) return;
    const selection=gate.begin({token,run:uuid,preview});
    if (!selection) return;
    const current=generation.current;
    reviewedRef.current=false;
    setMessage('');
    let reportId:string;
    try {reportId=await postReport(selection);}
    catch(error) {
      gate.finish(error);
      if(current===generation.current) {setReviewed(false);setMessage(errorMessage(error));}
      return;
    }
    gate.finish();
    if(current===generation.current) {
      setPreview(null);setReviewed(false);setMessage(`Stored report ${reportId}; reading exact bytes…`);
      refresh();
    }
    try {
      const raw=await getRawReport(selection.token,selection.run,reportId);
      if (raw !== selection.preview.raw) throw new Error("Stored bytes differ from the reviewed report");
      if(current===generation.current) {setRawRead(raw);setMessage(`Stored report ${reportId}`);}
    } catch(error) {if(current===generation.current)setMessage(`Stored report ${reportId}; read failed: ${errorMessage(error)}`);}
  }
  return <div>
    <h5>Upload author report</h5>
    <p className="muted">The author report is separate from scientific acceptance. Syntax and anchor preview do not validate the backend binding.</p>
    <input aria-label="Report JSON file" type="file" accept=".json,application/json" onChange={e=>void choose(e.target.files?.[0])}/>
    {preview && <>
      <p>Bytes: {preview.bytes} · SHA-256: <code>{preview.sha256}</code></p>
      <p>Run: <code>{preview.anchor.run_uuid}</code> · Definition: {preview.anchor.root_definition_id} ({preview.anchor.root_kind}) · Plan: <code>{preview.anchor.plan_fingerprint}</code></p>
      <h5>Exact report text, including used runs</h5><pre style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{preview.raw}</pre>
      <label><input type="checkbox" checked={reviewed} onChange={e=>{reviewedRef.current=e.target.checked;setReviewed(e.target.checked);}}/> I reviewed this exact report</label>
      <button type="button" disabled={!reviewed || gate.blocked} onClick={()=>void submit()}>Save report once</button>
    </>}
    {gate.uncertain && <p role="alert">The write outcome is unknown. Do not retry; inspect the operation with an operator. {gate.diagnostic}</p>}
    {message && <p role="status">{message}</p>}
    {rawRead && <><h5>Exact stored report</h5><pre style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{rawRead}</pre></>}
  </div>;
}
