import { useEffect, useRef, useState } from 'react'
import { Globe, Plus, Square, RefreshCw } from 'lucide-react'
import { getJSON } from './api.js'

const requestId=()=>crypto.randomUUID().replaceAll('-','')
const post=(url,body={})=>getJSON(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})

export default function BrowserPanel(){
  const [ready,setReady]=useState(false),[session,setSession]=useState(null),[frame,setFrame]=useState(null)
  const [address,setAddress]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false),[manual,setManual]=useState(false),[text,setText]=useState(''),[now,setNow]=useState(Date.now())
  const active=useRef(true),lock=useRef(false),stopping=useRef(false),sessionRef=useRef(null),tabRef=useRef(0)
  sessionRef.current=session;tabRef.current=frame?.active_tab||0
  const running=session?.status==='running'&&session.deadline*1000>now
  useEffect(()=>{active.current=true;getJSON('/api/browser/status').then(data=>{if(active.current){setReady(data.prepared);setSession(data.session)}}).catch(err=>{if(active.current)setError(err.message)});const timer=setInterval(()=>setNow(Date.now()),1000);return()=>{active.current=false;clearInterval(timer)}},[])
  async function action(operation,values={}){
    const current=sessionRef.current
    if(lock.current||stopping.current||current?.status!=='running')return
    lock.current=true;setBusy(true)
    try{
      const data=await post(`/api/browser/sessions/${current.id}/actions`,{operation,tab:tabRef.current,request_id:requestId(),...values})
      if(active.current&&!stopping.current&&sessionRef.current?.id===current.id){setFrame(data);setError('')}
    }catch(err){if(active.current&&!stopping.current)setError(err.message)}
    finally{lock.current=false;if(active.current)setBusy(false)}
  }
  useEffect(()=>{
    if(!running)return
    const timer=setInterval(()=>{if(!lock.current)action('capture')},3000)
    return()=>clearInterval(timer)
  },[running,session?.id])
  async function prepare(){setBusy(true);setError('');try{const data=await post('/api/browser/prepare');if(active.current)setReady(data.prepared)}catch(err){if(active.current)setError(err.message)}finally{if(active.current)setBusy(false)}}
  async function start(){stopping.current=false;setBusy(true);setError('');try{const data=await post('/api/browser/sessions',{request_id:requestId()});if(active.current){setSession(data);setFrame(null);setManual(false)}}catch(err){if(active.current)setError(err.message)}finally{if(active.current)setBusy(false)}}
  async function stop(){stopping.current=true;setManual(false);setBusy(true);try{const data=await post(`/api/browser/sessions/${session.id}/stop`);if(active.current){setSession(data);setFrame(null);setError('')}}catch(err){if(active.current)setError(err.message)}finally{if(active.current)setBusy(false)}}
  function navigate(event){event.preventDefault();let url=address.trim();if(!/^https?:\/\//i.test(url))url=`https://${url}`;action('navigate',{url})}
  function click(event){if(!manual||busy||!running)return;const rect=event.currentTarget.getBoundingClientRect();action('click',{x:Math.min(1279,(event.clientX-rect.left)/rect.width*1280),y:Math.min(719,(event.clientY-rect.top)/rect.height*720)})}
  return <section className="browser-panel live-browser">
    <div className="browser-session-bar"><Globe size={16}/><strong>Cloud browser</strong><span>{running?`${Math.max(0,Math.ceil((session.deadline*1000-now)/1000))}s remaining`:session?.status||'Not started'}</span>{running||session?.status==='starting'?<button onClick={stop}><Square size={13}/>Stop session</button>:<button disabled={busy} onClick={ready?start:prepare}>{busy?'Preparing...':ready?'Start session':'Prepare browser'}</button>}</div>
    <p className="browser-cost">Separate browser sessions run for up to 5 minutes. Session and preparation costs are billed by the hosting provider outside chat usage.</p>
    {error&&<p role="alert" className="diagnostic-error">{error}</p>}
    {running?<>
      <div className="browser-tabs">{(frame?.tabs||[]).map(tab=><button key={tab.index} disabled={busy} aria-pressed={tab.index===frame.active_tab} onClick={()=>action('select_tab',{tab:tab.index})}>{tab.title||'New tab'}</button>)}<button disabled={busy||(frame?.tabs?.length||0)>=6} aria-label="New browser tab" onClick={()=>action('new_tab')}><Plus size={14}/></button><button disabled={busy} onClick={()=>action('close_tab')}>Close tab</button></div>
      <form onSubmit={navigate}><input aria-label="Website address" placeholder="https://example.com" value={address} onChange={event=>setAddress(event.target.value)}/><button disabled={busy||!address.trim()}>Go</button><button type="button" disabled={busy} aria-label="Capture browser screenshot" onClick={()=>action('capture')}><RefreshCw size={14}/></button></form>
      <div className="browser-controls"><label><input type="checkbox" checked={manual} onChange={event=>setManual(event.target.checked)}/>Manual control</label><span>{frame?.url||'Waiting for first screenshot...'}</span>{frame&&<small>Captured {new Date(frame.captured_at*1000).toLocaleTimeString()}</small>}</div>
      {frame?.screenshot?<div className={`browser-screen ${manual?'manual':''}`}><img src={frame.screenshot} alt={`Remote browser: ${frame.title||'blank page'}`} onClick={click} draggable={false}/>{frame.pointer&&<span className="browser-pointer" style={{left:`${frame.pointer.x/12.8}%`,top:`${frame.pointer.y/7.2}%`}} aria-label="Last executed pointer position">&#8598;</span>}</div>:<div className="browser-disconnected">Waiting for the remote browser...</div>}
      {manual&&<form className="browser-controls" onSubmit={event=>{event.preventDefault();if(text){action('type',{text});setText('')}}}><input aria-label="Text to type into focused browser field" type="password" autoComplete="off" maxLength={2000} value={text} onChange={event=>setText(event.target.value)} placeholder="Text for the focused field"/><button disabled={busy||!text}>Type</button><button type="button" disabled={busy} onClick={()=>action('key',{key:'Enter'})}>Enter</button><button type="button" disabled={busy} onClick={()=>action('key',{key:'Tab'})}>Tab</button><button type="button" disabled={busy} onClick={()=>action('scroll',{delta:-600})}>Scroll up</button><button type="button" disabled={busy} onClick={()=>action('scroll',{delta:600})}>Scroll down</button></form>}
      <details className="browser-console"><summary>Browser console: {frame?.console?.length||0}</summary>{(frame?.console||[]).map((entry,index)=><pre key={index}>{entry.level}: {entry.text}</pre>)}</details>
    </>:<div className="browser-disconnected"><Globe size={34}/><h2>A separate browser for your workspace</h2><p>Open public websites, view real screenshots and use manual controls. Private network addresses and downloads are blocked. Sessions expire automatically; opening this panel does not start one.</p></div>}
    <p className="browser-cost">Browser beta: screenshots refresh every 3 seconds while this panel is open. Closing the panel does not stop the session; use Stop or allow its timer to expire. Agent access to page content is not connected yet.</p>
  </section>
}
