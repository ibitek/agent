import { serviceCost } from './AccountPanel.jsx'
import ApplicationBuild from './ApplicationBuild.jsx'
import { useEffect, useRef, useState } from 'react'
import { Activity, Bot, Check, CheckCircle2, Clock3, Code2, FileText, LoaderCircle, Square, Terminal, RotateCw, XCircle } from 'lucide-react'
import { getJSON } from './api.js'

function TaskView({ activeTask, events, logsReady, logsComplete, logRef, latestAssistant, cancelSelected, threadRuns, selectTask, hidePreview = false }) {
  if (!activeTask || !logsReady) return <div className="task-content" role="status"><LoaderCircle className="spin" size={18} /><p>Loading task status and execution log…</p></div>
  const isActive = activeTask.status === 'queued' || activeTask.status === 'running'
  const report = activeTask.report
  const costRecord = report?.cost_record || activeTask.cost_record
  const limits = report?.limits || {}
  const tokens = report?.tokens || {}
  const files = [...new Map([...(report?.files_modified || []).map((path) => [path, { path, action: 'modified' }]), ...(report?.files_created || []).map((path) => [path, { path, action: 'created' }])]).values()]
  const finalTestOutput = report?.final_test?.stdout_tail || report?.final_test?.stderr_tail || ''
  const passMatch = finalTestOutput.match(/(\d+)\s+passed\b/)
  const failMatch = finalTestOutput.match(/(\d+)\s+failed\b/)
  const finalAnswer = report?.attempt_details?.at(-1)?.final_answer
  const tokenValuesAvailable = Number.isFinite(tokens.prompt_tokens) || Number.isFinite(tokens.completion_tokens)
  return <section className="task-content">
    <div className="task-heading"><div><div className="eyebrow">TASK EXECUTION</div><h1>{activeTask.task}</h1><p className="task-meta"><span className={`status-dot ${activeTask.status}`} />{activeTask.status}<span>·</span>{activeTask.model}<span>·</span>{activeTask.workspace}</p></div>{activeTask.cancellation_supported && <button className="cancel-button" onClick={cancelSelected}><Square size={13} fill="currentColor" /> Cancel queued task</button>}</div>
    <div className={`result-card ${!isActive && report?.success ? 'success' : ''}`}><div className={`result-icon ${!isActive && report?.success ? 'success' : ''}`}>{isActive ? <LoaderCircle className="spin" size={18} /> : report?.success ? <CheckCircle2 size={18} /> : <XCircle size={18} />}</div><div><strong>{isActive ? (activeTask.status === 'queued' ? 'Queued for execution' : 'Agent is working') : report?.success ? (report.verification_level==='files_only'?'Application source ready':report.verification_level==='static'?'Website files ready':'Task completed and verified') : 'Task finished with issues'}</strong><p>{report?.success ? report.verification_level==='files_only' ? 'Source files saved. Compilation runs separately; browser tests are not yet verified.' : report.verification_level==='static' ? 'Static preview prepared. Browser interactions and automated tests were not run.' : `SYNVIO verified the task with ${passMatch ? `${passMatch[1]} passing test${passMatch[1] === '1' ? '' : 's'}` : 'a passing ground-truth test run'}${files.length ? ` and recorded ${files.length} changed file${files.length === 1 ? '' : 's'}` : ''}.` : report?.reason || activeTask.error || (isActive ? 'Live progress appears below from the agent event stream.' : 'No report was recorded for this task.')}{failMatch ? ` ${failMatch[1]} failing test${failMatch[1] === '1' ? '' : 's'}.` : ''}</p></div></div>
    {threadRuns.length > 1 && <nav className="thread-runs" aria-label="Conversation stages">{threadRuns.map((run,index)=><button key={run.id} type="button" aria-current={run.id===activeTask.id ? 'step' : undefined} onClick={()=>selectTask(run.id)}>{index+1}. {run.task}</button>)}</nav>}
    {activeTask.parent_id && <p className="task-meta">Continuing previous work from its saved project version</p>}
    {!hidePreview && <ProjectPreview key={activeTask.id} taskId={activeTask.id} revision={`${events.filter((event)=>event.event === 'preview_updated').length}:${activeTask.status}`} />}
    {activeTask.download_available && <a className="download-project" href={`/api/tasks/${activeTask.id}/download`}>Download project ZIP</a>}
    {report && <div className="metrics-row"><Metric label="Steps" value={Number.isFinite(report.steps_used) ? (Number.isFinite(limits.global_steps)?`${report.steps_used} / ${limits.global_steps}`:report.steps_used) : '—'} /><Metric label="Tool calls" value={Number.isFinite(report.tool_calls) ? (Number.isFinite(limits.global_tool_calls)?`${report.tool_calls} / ${limits.global_tool_calls}`:report.tool_calls) : '—'} /><Metric label="Attempts" value={Number.isFinite(report.attempts) ? (Number.isFinite(limits.max_repair_attempts)?`${report.attempts} / ${limits.max_repair_attempts}`:report.attempts) : '—'} /><Metric label="Duration" value={Number.isFinite(report.duration_seconds) ? `${report.duration_seconds.toFixed(1)}s` : '—'} /></div>}
    {report && <div className="result-details">
      {files.length > 0 && <div className="changed-files"><div className="section-heading"><div><FileText size={15} /><strong>Changed files</strong><span>({files.length})</span></div></div><div className="file-list">{files.map((file) => <FileResult key={`${file.action}:${file.path}`} taskId={activeTask.id} file={file} />)}</div></div>}
      {costRecord && <div className="usage-card"><div className="section-heading"><strong>AI usage · USD</strong></div><div className="usage-values"><Metric label="Budget" value={`$${Number(costRecord.service_budget_usd??Number(costRecord.budget_usd)*1.3).toFixed(4)}`} /><Metric label="Usage estimate" value={`$${serviceCost(costRecord).toFixed(6)}`} /><Metric label="Unresolved cost" value={`$${Number(costRecord.service_reserved_usd??Number(costRecord.unresolved_reservation_usd||0)*1.3).toFixed(6)}`} /></div><p className="composer-caption">Excludes infrastructure and tax. This is an estimate, not a balance charge. Unconfirmed usage remains reserved for review.</p></div>}
      {tokenValuesAvailable && <div className="usage-card"><div className="section-heading"><div><Activity size={15} /><strong>Model usage</strong></div></div><div className="usage-values">{Number.isFinite(tokens.prompt_tokens) && <Metric label="Input tokens" value={tokens.prompt_tokens.toLocaleString()} />}{Number.isFinite(tokens.completion_tokens) && <Metric label="Output tokens" value={tokens.completion_tokens.toLocaleString()} />}{Number.isFinite(tokens.total_tokens) && <Metric label="Total tokens" value={tokens.total_tokens.toLocaleString()} />}{!costRecord && Number.isFinite(report.estimated_cost_usd) && <Metric label="Estimated AI cost" value={`$${report.estimated_cost_usd.toFixed(6)}`} />}</div></div>}
    </div>}
    <div className="section-heading"><div><Terminal size={15} /><strong>Execution log</strong><span>({events.length})</span></div><span className="live-label"><span className={isActive ? 'pulse-dot' : ''} />{isActive ? 'Live progress' : logsComplete ? 'Complete' : 'Synchronizing execution log…'}</span></div>
    <div className="event-log" ref={logRef}>{events.length === 0 ? <div className="log-empty"><Clock3 size={16} />Waiting for the first backend event…</div> : events.map((event, index) => <EventRow key={event.sequence || index} event={event} />)}</div>
    {report?.tests?.map((test, index) => <div className="test-result" key={index}><FileText size={15} /><strong>{test.passed ? 'Tests passed' : 'Tests failed'}</strong><span>{test.stdout_tail || test.stderr_tail || 'Allowlisted test runner completed.'}</span>{test.passed ? <Check size={15} /> : <XCircle size={15} />}</div>)}
    {latestAssistant && finalAnswer && <div className="assistant-answer"><Bot size={17} /><div><strong>Agent final response</strong><p>{finalAnswer}</p></div></div>}
  </section>
}

function ProjectPreview({taskId,revision,initialOpen=false,onRuntime}) {
  const [open,setOpen] = useState(initialOpen)
  const [preview,setPreview] = useState(null)
  const [error,setError] = useState('')
  const [device,setDevice] = useState('desktop')
  const [buildRevision,setBuildRevision] = useState(0),[runtime,setRuntime]=useState([])
  const frame=useRef(null),runtimeItems=useRef([])
  useEffect(()=>{
    runtimeItems.current=[];setRuntime([]);onRuntime?.({taskId,items:[]})
    function receive(event){
      const data=event.data
      if(event.source!==frame.current?.contentWindow||!preview?.runtime_token||data?.type!=='synvio-preview-runtime'||data.token!==preview.runtime_token||!['error','warn','warning','empty','rendered'].includes(data.level)||typeof data.text!=='string')return
      const item={level:data.level,text:data.text.slice(0,800)}
      if(runtimeItems.current.some(previous=>previous.level===item.level&&previous.text===item.text))return
      const items=[...runtimeItems.current,item].slice(-20);runtimeItems.current=items;setRuntime(items);onRuntime?.({taskId,items})
    }
    window.addEventListener('message',receive);return()=>window.removeEventListener('message',receive)
  },[preview?.runtime_token,taskId,onRuntime])
  useEffect(()=>{
    if (!open) return
    let cancelled = false
    setError('')
    getJSON(`/api/tasks/${taskId}/preview`).then((value)=>{if(!cancelled) setPreview(value)}).catch((cause)=>{if(!cancelled) setError(cause.message)})
    return ()=>{cancelled=true}
  },[taskId,revision,open,buildRevision])
  return <section className="project-preview"><div className="preview-toolbar"><span className="preview-address">/</span><button type="button" aria-label="Reload application preview" onClick={()=>{setOpen(true);setBuildRevision(value=>value+1)}}><RotateCw size={14}/></button>{open && <select aria-label="Preview size" value={device} onChange={(event)=>setDevice(event.target.value)}><option value="desktop">Desktop</option><option value="mobile">Mobile</option></select>}</div>{open && <><p className="composer-caption">Your saved HTML, CSS and JavaScript, updated after file changes. This is your application preview, not the agent's browser.</p>{runtime.some(item=>['error','empty'].includes(item.level))&&<div className="preview-runtime-error" role="alert"><strong>Application preview needs attention</strong>{runtime.filter(item=>['error','empty'].includes(item.level)).slice(-3).map((item,index)=><p key={index}>{item.text}</p>)}{<small>Build success does not mean the application rendered. See Console for runtime details.</small>}</div>}{preview?.can_build&&<ApplicationBuild taskId={taskId} onReady={()=>setBuildRevision(value=>value+1)}/>} {error ? <p role="alert">{error}</p> : !preview ? <p role="status">Preparing preview…</p> : preview.available ? <div className={`preview-frame ${device}`}><iframe ref={frame} key={buildRevision} title="Application preview" sandbox="allow-scripts" referrerPolicy="no-referrer" srcDoc={preview.document}/></div> : <p className="preview-unavailable">{preview.reason}</p>}</>}</section>
}

function FileResult({ taskId, file }) {
  const [view, setView] = useState('file')
  const [content, setContent] = useState(null)
  const [diff, setDiff] = useState(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  async function load(nextView) {
    setView(nextView); setError(''); setLoading(true)
    try {
      const query = new URLSearchParams({ file: file.path })
      const result = await getJSON(`/api/tasks/${taskId}/files/${nextView === 'diff' ? 'diff' : 'content'}?${query}`)
      if (nextView === 'diff') setDiff(result)
      else setContent(result)
    } catch (cause) { setError(cause.message) }
    finally { setLoading(false) }
  }
  return <article className="file-result"><div className="file-result-head"><div><Code2 size={14} /><strong>{file.path}</strong><span className={`file-action ${file.action}`}>{file.action}</span></div><div className="file-actions"><button className={view === 'file' ? 'active' : ''} onClick={() => load('file')}>File</button><button className={view === 'diff' ? 'active' : ''} onClick={() => load('diff')}>Diff</button></div></div>{loading && <div className="file-view-state">Loading read-only view…</div>}{error && <div className="file-view-state error">{error}</div>}{view === 'file' && content && <pre className="code-view" aria-label={`Read-only file ${file.path}`}>{content.content}</pre>}{view === 'diff' && diff && (diff.available ? <pre className="diff-view" aria-label={`Read-only diff ${file.path}`}>{diff.diff || 'No textual differences.'}</pre> : <div className="file-view-state">{diff.reason}</div>)}</article>
}

function Metric({ label, value }) { return <div className="metric"><span>{label}</span><strong>{value}</strong></div> }

function EventRow({ event }) {
  const time = event.created_at ? new Date(event.created_at * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : event.ts ? new Date(event.ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '--:--:--'
  const displayEvent=event.event==='model_cost'?{currency:'USD',usage_estimate:serviceCost(event).toFixed(6),reserved:Number(event.service_reserved_usd??Number(event.unresolved_reservation_usd||0)*1.3).toFixed(6),calls:event.calls?.length||0}:event
  const tool = event.tool ? <><strong>{event.tool}</strong>{event.allowed === false && <em> blocked</em>}{event.success === false && <em> · failed</em>}{event.output && <pre>{event.output}</pre>}</> : Object.entries(displayEvent).filter(([key]) => !['sequence', 'created_at', 'ts', 'event'].includes(key)).map(([key, value]) => <span key={key}>{key}: {typeof value === 'string' ? value : JSON.stringify(value)}</span>)
  return <div className="event-row"><time>{time}</time><span className="event-marker"><span /></span><div className="event-body"><strong className="event-name">{event.event?.replaceAll('_', ' ') || 'event'}</strong>{tool && <div className="event-detail">{tool}</div>}</div></div>
}

export { TaskView, ProjectPreview }