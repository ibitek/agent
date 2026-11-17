import { useEffect, useState } from 'react'
import { getJSON } from './api.js'

export default function CodexBrowser({taskId,running=false,revision=0}){
  const [screen,setScreen]=useState(null),[error,setError]=useState(''),[viewport,setViewport]=useState('desktop')
  useEffect(()=>{
    let stopped=false,timer,inFlight=false
    const controller=new AbortController()
    async function refresh(){
      clearTimeout(timer)
      if(stopped||document.hidden||inFlight)return
      inFlight=true
      try{const result=await getJSON(`/api/tasks/${taskId}/codex-screen`,{signal:controller.signal});if(!stopped){setScreen(result);setError('')}}catch(cause){if(!stopped)setError(cause.message)}
      finally{inFlight=false}
      if(!stopped&&running&&!document.hidden)timer=setTimeout(refresh,5000)
    }
    document.addEventListener('visibilitychange',refresh)
    refresh();return()=>{stopped=true;controller.abort();clearTimeout(timer);document.removeEventListener('visibilitychange',refresh)}
  },[taskId,running,revision])
  return <section className="codex-browser"><h2>Agent browser</h2><p>Saved screenshots from the agent's separate cloud workspace. This is a snapshot view, not continuous video.</p>{error&&<p role="alert">{error}</p>}{screen?.image?<>{screen.mobile_image&&<div className="live-controls" role="group" aria-label="Screenshot viewport">{['desktop','mobile'].map(value=><button key={value} aria-pressed={viewport===value} onClick={()=>setViewport(value)}>{value}</button>)}</div>}<img style={{width:'100%',borderRadius:12}} src={viewport==='mobile'&&screen.mobile_image?screen.mobile_image:screen.image} alt={`Latest ${viewport} screenshot captured by Codex`}/><small>Captured {new Date(screen.updated_at*1000).toLocaleTimeString()}</small></>:<p>No browser screenshot yet. A screenshot appears only after the agent captures one. Opening this view does not provision a browser.</p>}</section>
}
