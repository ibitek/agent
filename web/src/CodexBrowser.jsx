import { useEffect, useState } from 'react'
import { getJSON } from './api.js'

export default function CodexBrowser({taskId}){
  const [screen,setScreen]=useState(null),[error,setError]=useState('')
  useEffect(()=>{
    let stopped=false,timer
    async function refresh(){
      try{const result=await getJSON(`/api/tasks/${taskId}/codex-screen`);if(!stopped){setScreen(result);setError('')}}catch(cause){if(!stopped)setError(cause.message)}
      if(!stopped)timer=setTimeout(refresh,5000)
    }
    refresh();return()=>{stopped=true;clearTimeout(timer)}
  },[taskId])
  return <section className="codex-browser"><h2>Codex browser</h2><p>Actual screenshots captured by the agent in its separate cloud workspace.</p>{error&&<p role="alert">{error}</p>}{screen?.image?<><img style={{width:'100%',borderRadius:12}} src={screen.image} alt="Latest browser screenshot captured by Codex"/><small>Captured {new Date(screen.updated_at*1000).toLocaleTimeString()}</small></>:<p>No browser screenshot yet. Select a Codex + Browser model and ask it to inspect the application. A terminal-only Codex workspace does not provision a desktop.</p>}</section>
}
