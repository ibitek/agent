import { useEffect, useRef, useState } from 'react'
import { Hammer, LoaderCircle } from 'lucide-react'
import { getJSON } from './api.js'

export default function ApplicationBuild({taskId,onReady}){
  const requestRef=useRef(null)
  const [state,setState]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState('')
  useEffect(()=>{let cancelled=false;Promise.all([getJSON(`/api/tasks/${taskId}/build-preview`),getJSON(`/api/tasks/${taskId}`)]).then(([value,task])=>{if(!cancelled){setState(value);if(value.status==='building')requestRef.current=value.request_id;setBusy(value.status==='building');if(value.status==='not_started'&&task.status==='completed'&&task.runtime==='app'&&['edit','manual_edit'].includes(task.operation||'edit'))start(taskId)}}).catch(()=>{});return()=>{cancelled=true}},[taskId])
  useEffect(()=>{
    if(!busy)return
    let cancelled=false,timer
    const poll=async()=>{try{const value=await getJSON(`/api/tasks/${taskId}/build-preview`);if(!cancelled&&value.request_id===requestRef.current){setState(value);if(['ready','failed'].includes(value.status)){setBusy(false);setBusy(value.status==='building');if(value.status==='ready')onReady()}}}catch{/* The active POST still owns the build. */}if(!cancelled)timer=setTimeout(poll,1200)}
    poll();return()=>{cancelled=true;clearTimeout(timer)}
  },[busy,taskId])
  async function start(automaticId){
    requestRef.current=typeof automaticId==='string'?automaticId:crypto.randomUUID().replaceAll('-','')
    setBusy(true);setError('')
    try{
      const value=await getJSON(`/api/tasks/${taskId}/build-preview`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({request_id:requestRef.current})})
      setState(value)
      setBusy(value.status==='building');if(value.status==='ready')onReady()
      getJSON(`/api/tasks/${taskId}/build-preview`).then(setState).catch(()=>{})
    }catch(cause){setError(cause.message);setBusy(false);try{const current=await getJSON(`/api/tasks/${taskId}/build-preview`);setState(current);if(current.status==='building'){requestRef.current=current.request_id;setBusy(true);setError('')}}catch{}}
  }
  const events=state?.events||[],last=events.at(-1)
  return <section className="application-build compact-build"><div className="build-status-row"><Hammer size={14}/><strong>{state?.status==='ready'?'Build passed':busy?'Building application…':'Application needs a build'}</strong>{state?.status!=='ready'&&<button type="button" disabled={busy} onClick={start}>{busy?<LoaderCircle size={14} className="spin"/>:'Retry build'}</button>}<span className="build-status-note">{state?.status==='ready'?'Browser tests not run':'Cloud environment'}</span></div>{(error||state?.error)&&<p role="alert" className="work-error">{error||state.error}</p>}<details className="build-diagnostics"><summary>Build details &amp; logs</summary><p>Compiles React/Vite in isolation. Compute estimate: $0.005/minute, measured per second. Infrastructure is separate from model usage. No model call.</p><small>1 vCPU · up to 160 seconds · 4 builds/day · environment stops after the build.</small>{last&&<p role="status">{last.phase?.replaceAll('_',' ')}</p>}{events.filter(event=>event.output).map(event=><pre key={event.sequence}>{event.output}</pre>)}</details></section>
}
