import { useEffect, useMemo, useRef, useState } from 'react'
import { Activity, ArrowDown, Camera, CheckCircle2, FileCode2, Terminal, AlertCircle } from 'lucide-react'
import CodexBrowser from './CodexBrowser.jsx'
import { activityItems } from './liveActivity.js'
import { getJSON } from './api.js'

const icons = {files:FileCode2, commands:Terminal, browser:Camera, checks:CheckCircle2}
const filters = ['all', 'files', 'commands', 'browser', 'checks']

function SavedImage({ taskId, path }) {
  const [source, setSource] = useState('')
  useEffect(() => {
    const controller = new AbortController()
    const query = new URLSearchParams({ file: path })
    getJSON('/api/tasks/' + taskId + '/project-files/content?' + query, { signal: controller.signal })
      .then(({ content }) => {
        if (!controller.signal.aborted && typeof content === 'string') {
          setSource('data:image/svg+xml;charset=utf-8,' + encodeURIComponent(content))
        }
      }).catch(() => {})
    return () => controller.abort()
  }, [taskId, path])
  return source && <img className="live-image-thumbnail" src={source} alt={'Image saved at ' + path} onError={() => setSource('')} />
}

export default function LivePanel({task, events = [], logsReady, logsComplete, onOpenFile}) {
  const [filter, setFilter] = useState('all'), [follow, setFollow] = useState(true)
  const end = useRef(null)
  const items = useMemo(() => activityItems(events), [events])
  const shown = items.filter(item => filter === 'all' || item.category === filter).slice(-200)
  const thumbnails = new Set(shown.filter(item => item.imageSaved).slice(-4).map(item => item.key))
  const running = ['queued', 'running'].includes(task?.status)
  const browser = task?.model_id?.startsWith('codex-browser:') || events.some(event=>event.event==='codex_workspace'&&event.browser_enabled)
  useEffect(() => {
    if (follow) end.current?.scrollIntoView({block:'nearest', behavior:'instant'})
  }, [items.length, follow, filter])
  return <section className="live-panel" aria-label="Live workspace activity">
    <header className="live-heading"><div><span className={`live-indicator ${running?'is-running':''}`}/><h2>{running?'Workspace activity':'Recorded activity'}</h2></div><span>{task?.status==='queued'?'Queued':task?.status==='running'?'Run in progress':task?.status==='completed'?'Run completed':task?.status==='paused'?'Work paused':task?.status==='failed'?'Run failed':'Saved run'}</span></header>
    <p className="live-caption">Actual file operations, commands and screenshots from this run. Opening Live does not start an agent or allocate an environment.</p>
    <div className="live-controls"><div role="group" aria-label="Filter activity">{filters.map(name=><button key={name} aria-pressed={filter===name} onClick={()=>setFilter(name)}>{name}<span>{name==='all'?items.length:items.filter(item=>item.category===name).length}</span></button>)}</div><button className="live-follow" aria-pressed={follow} onClick={()=>setFollow(value=>!value)}><ArrowDown size={13}/>{follow?'Following':'Follow activity'}</button></div>
    <div className={`live-layout ${browser?'with-browser':''}`}><div className="live-feed" onScroll={event=>{const el=event.currentTarget;if(el.scrollHeight-el.scrollTop-el.clientHeight>100)setFollow(false)}}>
      {!logsReady?<div className="live-empty"><Activity size={24}/><h3>Loading recorded activity</h3><p>Waiting for the saved event stream.</p></div>:!shown.length?<div className="live-empty"><Activity size={24}/><h3>{filter==='all'?'No execution events recorded yet':'No matching activity'}</h3><p>{running?'Events will appear when the execution environment reports them.':'This run has no recorded actions in this view. No browser session or terminal activity is implied.'}</p></div>:<ol aria-label="Execution timeline">{shown.map(item=>{const Icon=icons[item.category];return <li key={item.key} className={`live-entry is-${item.state}`}><div className="live-entry-icon">{item.state==='error'?<AlertCircle size={15}/>:<Icon size={15}/>}</div><div className="live-entry-body"><div className="live-entry-title"><strong>{item.title}</strong><time>{item.time?new Date(item.time*1000).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',second:'2-digit'}):'—'}</time></div>{item.detail&&<p>{item.detail}</p>}{task?.id&&thumbnails.has(item.key)&&<SavedImage taskId={task.id} path={item.path}/>} {item.path&&<button className="live-file-link" onClick={()=>onOpenFile(item.path)}>Open in Code &amp; files</button>}{item.output&&<details><summary>Command output</summary><pre>{item.output}</pre></details>}</div></li>})}</ol>}
      <div ref={end}/></div>{browser?<aside className="live-browser"><CodexBrowser key={task.id} taskId={task.id} running={running} revision={events.filter(event=>event.event==='browser_screenshot').length}/></aside>:<aside className="live-browser-note"><Camera size={17}/><div><strong>Browser view</strong><p>No agent browser recording is connected to this run. Application Preview is separate from the agent's browser.</p></div></aside>}</div>
    <footer className="live-footer">{shown.length<items.filter(item=>filter==='all'||item.category===filter).length?'Showing the latest 200 matching actions. Full records remain in Activity.':logsComplete?'Saved event stream loaded.':running?'Following the execution event stream.':'Finishing event synchronization.'}</footer>
  </section>
}
