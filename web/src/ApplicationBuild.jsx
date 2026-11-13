import { useEffect, useRef, useState } from 'react'
import { Hammer, LoaderCircle } from 'lucide-react'
import { getJSON } from './api.js'

export default function ApplicationBuild({taskId,onReady}){
  const requestRef=useRef(null)
  const [state,setState]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState('')
  useEffect(()=>{let cancelled=false;getJSON(`/api/tasks/${taskId}/build-preview`).then(value=>{if(!cancelled){setState(value);if(value.status==='building')requestRef.current=value.request_id;setBusy(value.status==='building')}}).catch(()=>{});return()=>{cancelled=true}},[taskId])
  useEffect(()=>{
    if(!busy)return
    let cancelled=false,timer
    const poll=async()=>{try{const value=await getJSON(`/api/tasks/${taskId}/build-preview`);if(!cancelled&&value.request_id===requestRef.current){setState(value);if(['ready','failed'].includes(value.status)){setBusy(false);if(value.status==='ready')onReady()}}}catch{/* The active POST still owns the build. */}if(!cancelled)timer=setTimeout(poll,1200)}
    poll();return()=>{cancelled=true;clearTimeout(timer)}
  },[busy,taskId])
  async function start(){
    requestRef.current=crypto.randomUUID().replaceAll('-','')
    setBusy(true);setError('')
    try{
      const value=await getJSON(`/api/tasks/${taskId}/build-preview`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({request_id:requestRef.current})})
      setState(value)
      if(value.status==='ready')onReady()
      getJSON(`/api/tasks/${taskId}/build-preview`).then(setState).catch(()=>{})
    }catch(cause){setError(cause.message)}finally{setBusy(false)}
  }
  const events=state?.events||[],last=events.at(-1)
  return <section className="application-build"><div><Hammer size={16}/><strong>Application build</strong></div><p>Compile a React/Vite project in an isolated cloud environment. No model call. Uses infrastructure allowance outside your AI budget.</p><button type="button" disabled={busy||state?.status==='ready'} onClick={start}>{busy?<><LoaderCircle size={14} className="spin"/>Building…</>:state?.status==='ready'?'Build passed':'Build preview'}</button><small>1 vCPU · up to 160 seconds · 4 builds/day · environment stops after the build.</small>{(error||state?.error)&&<p role="alert" className="work-error">{error||state.error}</p>}{last&&<p role="status">{last.phase?.replaceAll('_',' ')}</p>}{events.filter(event=>event.output).map(event=><pre key={event.sequence}>{event.output}</pre>)}{state?.status==='ready'&&<p>Production build passed. Browser interactions have not been tested.</p>}</section>
}
