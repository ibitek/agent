import { useCallback, useEffect, useRef, useState } from 'react'
import {
  Activity, ArrowUp, Bot, Check, CheckCircle2, ChevronDown, Circle,
  Clock3, Code2, FileText, FolderOpen, LoaderCircle, PanelLeftClose,
  Plus, ShieldCheck, Square, Terminal, XCircle,
} from 'lucide-react'

import { mergeTasks, watchTask } from './taskPolling.js'

const API = '/api'
const getJSON = async (url, options) => {
  const response = await fetch(url, options)
  const body = await response.json()
  if (response.status === 401) window.dispatchEvent(new Event('synvio-session-expired'))
  if (!response.ok) throw new Error(body.detail || `Request failed (${response.status})`)
  return body
}

function App() {
  const [session, setSession] = useState(null)
  const [health, setHealth] = useState(null)
  const [workspaces, setWorkspaces] = useState([])
  const [models, setModels] = useState([])
  const [limits, setLimits] = useState(null)
  const [tasks, setTasks] = useState([])
  const [selected, setSelected] = useState(null)
  const [eventState, setEventState] = useState({ taskId: null, items: [], complete: false })
  const [workspace, setWorkspace] = useState('')
  const [modelId, setModelId] = useState('openai:low')
  const [mode, setMode] = useState('chat')
  const [modelBudget,setModelBudget] = useState('0.10')
  const [conversation, setConversation] = useState([])
  const [prompt, setPrompt] = useState('')
  const [loading, setLoading] = useState(false)
  const [preparing, setPreparing] = useState(false)
  const [showEnvironments,setShowEnvironments] = useState(false)
  const [error, setError] = useState('')
  const logRef = useRef(null)

  useEffect(() => {
    let cancelled = false
    fetch('/api/session').then(async (response) => {
      if (response.status === 404) return {authenticated:true,cloud:false,configured:true}
      if (!response.ok) throw Error('Nie można sprawdzić sesji. Odśwież stronę.')
      return response.json()
    }).then((value) => { if (!cancelled) setSession(value) }).catch((cause) => { if (!cancelled) setError(cause.message) })
    const expired = () => setSession((value) => ({...value,authenticated:false}))
    window.addEventListener('synvio-session-expired', expired)
    return () => { cancelled = true; window.removeEventListener('synvio-session-expired',expired) }
  }, [])

  const refreshHistory = useCallback(async () => {
    const data = await getJSON(`${API}/tasks`)
    setTasks((previous) => mergeTasks(previous, data.items))
    return data.items
  }, [])

  useEffect(() => {
    if (!session?.authenticated) return
    Promise.all([
      getJSON(`${API}/health`), getJSON(`${API}/workspaces`),
      getJSON(`${API}/models`), getJSON(`${API}/limits`), refreshHistory(),
    ]).then(([h, w, m, l]) => {
      setHealth(h); setWorkspaces(w.items); setModels(m.items); setLimits(l)
      if (!m.items.some((item) => item.id === 'openai:low' && item.available)) {
        const firstAvailable = m.items.find((item) => item.available)
        if (firstAvailable) setModelId(firstAvailable.id)
      }
      setWorkspace(w.items[0] || '')
    }).catch((cause) => setError(session.cloud ? cause.message : `${cause.message}. Uruchom lokalne API SYNVIO.`))
  }, [refreshHistory, session?.authenticated])

  const selectTask = useCallback((id) => {
    setSelected(id)
  }, [])

  const events = eventState.taskId === selected ? eventState.items : []
  const logsReady = eventState.taskId === selected
  const logsComplete = logsReady && eventState.complete

  useEffect(() => {
    if (!selected || !session?.authenticated) return undefined
    return watchTask(selected, {
      getJSON,
      onSnapshot: ({ task, events: items, complete }) => {
        setEventState({ taskId: selected, items, complete })
        setTasks((previous) => mergeTasks(previous, [task]))
      },
    })
  }, [selected, session?.authenticated])

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight
  }, [events])

  async function submitTask(event) {
    event.preventDefault()
    if (!prompt.trim() || (mode === 'task' && !workspace) || loading) return
    setLoading(true); setError('')
    try {
      if (mode === 'chat') {
        const message = prompt.trim()
        const reply = await getJSON(`${API}/chat`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ model_budget_usd: Number(modelBudget), message, model_id: modelId, history: conversation.slice(-12).map(({role,content}) => ({role,content})) }),
        })
        setConversation((previous) => [...previous, {role:'user',content:message}, {role:'assistant',content:reply.content,cost:reply.estimated_cost_usd,local:reply.local_response}])
        setPrompt('')
        return
      }
      const created = await getJSON(`${API}/tasks`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model_budget_usd: Number(modelBudget), task: prompt.trim(), workspace, model_id: modelId }),
      })
      setTasks((previous) => mergeTasks(previous, [{
        ...created, task: prompt.trim(), workspace,
        model: models.find((item) => item.id === modelId)?.model || modelId,
        created_at: Date.now() / 1000, updated_at: 0,
      }]))
      setPrompt('')
      selectTask(created.id)
      if (created.run_required) {
        // Keep an active request for this one job; never retry a paid run automatically.
        getJSON(`${API}/tasks/${created.id}/run`, { method:'POST' }).catch((cause) => setError(cause.message))
      }
      refreshHistory().catch(() => {})
    } catch (cause) { setError(cause.message) }
    finally { setLoading(false) }
  }

  async function submitFollowup(event) {
    event.preventDefault()
    const parent = tasks.find((task)=>task.id === selected)
    if (!parent || !prompt.trim() || loading) return
    setLoading(true); setError('')
    try {
      const text = prompt.trim()
      const created = await getJSON('/api/tasks', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({task:text,parent_id:parent.id,workspace:parent.workspace,model_id:modelId,model_budget_usd:Number(modelBudget)})})
      setTasks((previous)=>mergeTasks(previous,[{...created,task:text,workspace:parent.workspace,parent_id:parent.id,thread_id:parent.thread_id || parent.id,model:models.find((item)=>item.id===modelId)?.model || modelId,created_at:Date.now()/1000,updated_at:0}]))
      setPrompt(''); selectTask(created.id)
      if (created.run_required) getJSON(`/api/tasks/${created.id}/run`,{method:'POST'}).catch((cause)=>setError(cause.message))
      refreshHistory().catch(()=>{})
    } catch(cause) { setError(cause.message) }
    finally { setLoading(false) }
  }

  async function prepareSandbox() {
    setPreparing(true); setError('')
    try {
      await getJSON('/api/setup/sandbox',{method:'POST'})
      const [nextHealth,nextWorkspaces] = await Promise.all([getJSON('/api/health'),getJSON('/api/workspaces')])
      setHealth(nextHealth);setWorkspaces(nextWorkspaces.items);setWorkspace(nextWorkspaces.items[0] || '')
    } catch(cause) { setError(cause.message) }
    finally { setPreparing(false) }
  }

  async function cancelSelected() {
    if (!selected) return
    try {
      await getJSON(`${API}/tasks/${selected}/cancel`, { method: 'POST' })
      await refreshHistory()
    } catch (cause) { setError(cause.message) }
  }

  const activeTask = tasks.find((task) => task.id === selected)
  const groupedThreads = Object.values(tasks.reduce((groups,task)=>{
    const id = task.thread_id || task.id
    if (!groups[id]) groups[id] = {id,latest:task,count:0,title:tasks.find((item)=>item.id===id)?.task || task.task}
    if (task.created_at > groups[id].latest.created_at) groups[id].latest = task
    groups[id].count += 1
    return groups
  },{})).sort((a,b)=>b.latest.created_at-a.latest.created_at)
  const threadRuns = activeTask ? tasks.filter((task)=>(task.thread_id || task.id)===(activeTask.thread_id || activeTask.id)).sort((a,b)=>a.created_at-b.created_at) : []
  const canContinueThread = activeTask && threadRuns.filter((task)=>task.report).at(-1)?.id === activeTask.id && !threadRuns.some((task)=>['queued','running'].includes(task.status))
  const latestAssistant = [...events].reverse().find((event) => event.event === 'task_finished')

  if (!session) return <main className="login-page"><div className="login-card" role="status"><Code2 size={28}/><h1>SYNVIO Core</h1><p>{error || 'Łączenie z serwerem…'}</p></div></main>
  if (!session.authenticated) return <LoginPanel session={session} onLogin={setSession} />
  const tasksAllowed = health?.tasks_enabled !== false
  const taskUnavailableReason = health?.tasks_unavailable_reason

  return (
    <div className="app-shell">
      {showEnvironments && <EnvironmentPanel health={health} onClose={()=>setShowEnvironments(false)} />}
      <aside className="sidebar">
        <div className="brand"><div className="brand-mark"><Code2 size={17} /></div><div><strong>SYNVIO</strong><span>CORE</span></div><button className="icon-button sidebar-toggle" aria-label="Close sidebar"><PanelLeftClose size={17} /></button></div>
        <button className="new-task" onClick={() => { setSelected(null); setEventState({ taskId: null, items: [], complete: false }); setPrompt(''); setError(''); setMode('chat'); setConversation([]) }}><Plus size={16} /> Nowa rozmowa <span>⌘ K</span></button>
        <div className="side-label">WORKSPACE</div>
        <div className="workspace-current"><FolderOpen size={15} /><select aria-label="Workspace" value={workspace} onChange={(event) => setWorkspace(event.target.value)} disabled={!workspaces.length}><option value="">No allowed workspace</option>{workspaces.map((path) => <option key={path} value={path}>{path.split(/[\\/]/).filter(Boolean).at(-1)}</option>)}</select><ChevronDown size={14} /></div>
        <div className="side-label history-label">ROZMOWY Z AGENTEM <span>{groupedThreads.length}</span></div>
        <nav className="history-list" aria-label="Task history">
          {tasks.length === 0 && <div className="empty-history">Your task history<br />will appear here.</div>}
          {groupedThreads.map((thread) => <button key={thread.id} className={`history-item ${activeTask && (activeTask.thread_id || activeTask.id) === thread.id ? 'selected' : ''}`} onClick={() => selectTask(thread.latest.id)}><span className={`status-dot ${thread.latest.status}`} /> <span className="history-copy"><span>{thread.title}</span><small>{thread.count > 1 ? `Etapy: ${thread.count} · ` : ''}{new Date(thread.latest.created_at * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}</small></span></button>)}
        </nav>
        <div className="sidebar-bottom"><div className="local-badge"><ShieldCheck size={15} /><span>{session.cloud ? 'Projekt w chmurze' : 'Local workspace'}</span><span className="online-dot" /></div><div className="user-row"><div className="avatar">S</div><div><strong>SYNVIO Core</strong><small>{session.cloud ? 'Prywatna sesja' : 'Local environment'}</small></div><button className="icon-button" aria-label="More options"><Circle size={16} /></button></div></div>
      </aside>

      <main className="main-panel">
        <header className="topbar"><div className="breadcrumbs"><span>Workspace</span><span className="crumb-slash">/</span><strong>{activeTask ? (activeTask.task.length > 48 ? `${activeTask.task.slice(0, 48)}…` : activeTask.task) : 'New task'}</strong></div><div className="top-actions"><button className="logout-button" type="button" onClick={()=>setShowEnvironments(true)}>Środowiska</button><span className={`connection ${health?.status === 'ok' ? 'connected' : ''}`}><span />{health?.status === 'ok' ? (session.cloud ? 'API connected' : 'Local API connected') : 'Connecting…'}</span>{session.cloud && <button className="logout-button" onClick={async () => { await getJSON('/api/logout',{method:'POST'}); setSelected(null); setConversation([]); setTasks([]); setSession({...session,authenticated:false}) }}>Wyloguj</button>}<button className="icon-button" aria-label="Activity"><Activity size={17} /></button><div className="avatar small">S</div></div></header>

        {error && <div className="error-banner"><XCircle size={16} /><span>{error}</span><button onClick={() => setError('')} aria-label="Dismiss error"><XCircle size={15} /></button></div>}

        {!selected && !activeTask ? <section className="welcome"><div className="welcome-icon"><Bot size={23} /></div><div className="eyebrow">{session.cloud ? 'SYNVIO AGENTS' : 'LOCAL AGENT WORKSPACE'}</div><div className="interaction-modes" role="group" aria-label="Tryb pracy"><button type="button" aria-pressed={mode === 'chat'} onClick={() => setMode('chat')}>Rozmowa</button><button type="button" aria-pressed={mode === 'task'} onClick={() => setMode('task')}>Zadanie</button></div><h1>{mode === 'chat' ? <>Porozmawiajmy.</> : <>Co mamy<br /><em>zbudować?</em></>}</h1><p>{mode === 'chat' ? 'Rozmowa bez dostępu do plików, narzędzi i testów. Zwykłe powitania są bezpłatne; pozostałe wiadomości korzystają z wybranego modelu.' : 'Agent może zmieniać pliki w wybranym projekcie i uruchamiać testy. Każde wysłane zadanie może naliczyć koszt API.'}</p>{mode === 'chat' && conversation.length > 0 && <div className="conversation" aria-label="Rozmowa">{conversation.map((message,index) => <article className={`chat-message ${message.role}`} key={index}><strong>{message.role === 'user' ? 'Ty' : 'SYNVIO'}</strong><p>{message.content}</p>{message.role === 'assistant' && <small>{message.local ? 'Odpowiedź lokalna · $0 · bez narzędzi' : `Bez narzędzi${Number.isFinite(message.cost) ? ` · szac. $${message.cost.toFixed(6)}` : ''}`}</small>}</article>)}</div>}{mode === 'task' && health?.can_prepare_tasks && <div className="sandbox-setup"><p>Jednorazowo przygotuj izolowane środowisko Pythona. Ta operacja zużywa limit Vercel Sandbox, ale nie uruchamia modelu AI.</p><button type="button" disabled={preparing} onClick={prepareSandbox}>{preparing ? 'Przygotowywanie…' : 'Przygotuj środowisko testów'}</button></div>}<TaskComposer {...{prompt,setPrompt,submitTask,workspace,modelId,setModelId,models,workspaces,loading,limits,mode,tasksAllowed,taskUnavailableReason,modelBudget,setModelBudget}} /></section> : <><TaskView {...{activeTask,events,logsReady,logsComplete,logRef,latestAssistant,cancelSelected,threadRuns,selectTask}} />{canContinueThread && <section className="followup-panel"><h2>Kontynuuj tę pracę</h2><p>Agent otrzyma zapisane pliki i skrót poprzednich poleceń. Kontynuacja ma osobny budżet modelu.</p><TaskComposer {...{prompt,setPrompt,workspace,modelId,setModelId,models,workspaces,loading,limits,modelBudget,setModelBudget,tasksAllowed,taskUnavailableReason}} mode="task" submitTask={submitFollowup}/></section>}</>}

        <footer className="footer"><span><ShieldCheck size={13} /> {session.cloud ? 'Projekty zapisują się w Twoim magazynie danych' : 'Tasks stay on this machine'}</span><span>Local agent runtime <i /> SYNVIO Core</span></footer>
      </main>
    </div>
  )
}

function EnvironmentPanel({health,onClose}) {
  const dialog = useRef(null)
  useEffect(()=>{dialog.current?.showModal()},[])
  return <dialog className="environment-dialog" ref={dialog} aria-labelledby="environments-title" onCancel={onClose}><div className="preview-toolbar"><h2 id="environments-title">Środowiska pracy</h2><button type="button" autoFocus onClick={onClose} aria-label="Zamknij środowiska">Zamknij</button></div><p>Podgląd aplikacji, przeglądarka agenta i Twój komputer to osobne środowiska.</p><article><strong>Wykonywanie kodu</strong><span className={health?.tasks_enabled ? 'environment-ready' : ''}>{health?.tasks_enabled ? 'Gotowe' : 'Nie podłączono'}</span><p>Zmiany plików i testy projektu w odizolowanym środowisku.</p></article><article><strong>Przeglądarka w chmurze</strong><span>Nie podłączono</span><p>Docelowo zobaczysz rzeczywisty obraz przeglądarki oraz działania agenta na stronach.</p></article><article><strong>Agent do VS Code</strong><span>Wersja testowa 0.1.0</span><p>Odczyt kodu wybranego projektu, follow-upy i zmiany zatwierdzane w podglądzie różnic. Klucz modelu pozostaje na serwerze.</p><a className="download-project" href="/downloads/synvio-workspace-0.1.0.vsix" download>Pobierz wtyczkę VS Code (.vsix)</a><p>W VS Code: Extensions → … → Install from VSIX, potem polecenie SYNVIO: Open Agent.</p></article><article><strong>Twój komputer</strong><span>Nie podłączono</span><p>Ten tryb wymaga lokalnej aplikacji. Dostęp do ekranu, myszy i klawiatury pozostaje wyłączony. Po wdrożeniu połączenia będzie można je przerwać przyciskiem Zatrzymaj.</p></article><p className="composer-caption">Samo otwarcie tego panelu nie uruchamia sesji ani płatnych usług.</p></dialog>
}

function LoginPanel({session,onLogin}) {
  const [email,setEmail] = useState('')
  const [password,setPassword] = useState('')
  const [busy,setBusy] = useState(false)
  const [error,setError] = useState('')
  async function login(event) {
    event.preventDefault(); setBusy(true); setError('')
    try {
      const response = await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email,password})})
      const result = await response.json()
      if (!response.ok) throw Error(result.detail || 'Logowanie nie powiodło się.')
      setPassword(''); onLogin({...session,...result})
    } catch(cause) { setError(cause.message) }
    finally { setBusy(false) }
  }
  return <main className="login-page"><form className="login-card" onSubmit={login}><div className="brand-mark"><Code2 size={22}/></div><div className="eyebrow">SYNVIO AGENTS</div><h1>Twoja przestrzeń<br/>do pracy z AI.</h1><p>Zaloguj się, aby rozmawiać i tworzyć projekty. Klucze API pozostają na serwerze.</p>{!session.configured && <div className="model-warning">Logowanie nie jest jeszcze skonfigurowane. Administrator musi podłączyć usługę logowania.</div>}{session.auth_provider === 'supabase' && <><label htmlFor="login-email">E-mail</label><input id="login-email" type="email" autoComplete="username" required value={email} onChange={(event)=>setEmail(event.target.value)} disabled={!session.configured || busy}/></>}<label htmlFor="login-password">Hasło</label><input id="login-password" type="password" autoComplete="current-password" value={password} onChange={(event)=>setPassword(event.target.value)} disabled={!session.configured || busy}/>{error && <p role="alert" className="model-warning">{error}</p>}<button type="submit" disabled={!session.configured || busy || !password}>{busy ? 'Logowanie…' : 'Wejdź do SYNVIO'}</button></form></main>
}


function TaskComposer({ prompt, setPrompt, submitTask, workspace, modelId, setModelId, models, workspaces, loading, limits, mode, tasksAllowed = true, taskUnavailableReason, modelBudget, setModelBudget }) {
  const selectedModel = models.find((item) => item.id === modelId)
  const availableModels = models.filter((item) => item.available)
  return <form className="composer-wrap" onSubmit={submitTask}>
    <label className="limits-note">Limit kosztu modelu (USD) <input aria-label="Limit kosztu modelu w USD" type="number" min="0.000001" max="1" step="0.000001" required value={modelBudget} onChange={(event)=>setModelBudget(event.target.value)} /></label><textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} placeholder={mode === 'chat' ? 'Napisz wiadomość…' : 'Opisz konkretne zmiany do wykonania…'} rows={3} maxLength={10000} aria-label={mode === 'chat' ? 'Wiadomość' : 'Task description'} />
    <div className="composer-bottom"><div className="composer-options"><label className="select-pill"><Bot size={14} /><select value={modelId} onChange={(event) => setModelId(event.target.value)} aria-label="AI model" disabled={!models.length}>{models.map((item) => <option value={item.id} key={item.id} disabled={!item.available}>{item.label}{item.available ? '' : ' · unavailable'}</option>)}</select><ChevronDown size={13} /></label>{mode === 'task' && <span className="workspace-pill"><FolderOpen size={13} />{workspace ? workspace.split(/[\\/]/).filter(Boolean).at(-1) : 'Choose workspace'}</span>}</div><button className="send-button" disabled={loading || !prompt.trim() || (mode === 'task' && (!tasksAllowed || !workspaces.length || !selectedModel?.available))} aria-label={mode === 'chat' ? 'Wyślij wiadomość' : 'Start task'}>{loading ? <LoaderCircle className="spin" size={17} /> : <ArrowUp size={17} />}</button></div>
    {mode === 'task' && !tasksAllowed && <div className="model-warning">{taskUnavailableReason}</div>}
    {selectedModel && !selectedModel.available && <div className="model-warning">{selectedModel.unavailable_reason}</div>}
    {mode === 'task' && limits && <div className="limits-note"><span><Activity size={12} /> {limits.steps_per_attempt} steps</span><i /><span>{limits.tool_calls_per_task} tool calls</span><i /><span>{limits.attempts} attempt</span><i /><span>{limits.model_test_runs} model test runs</span></div>}
    <div className="composer-caption">{selectedModel ? <>Model: <b>{selectedModel.model}</b> · Provider: <b>{selectedModel.provider}</b></> : <>No available provider model is configured.</>} <span>·</span> Limit dotyczy modelu, bez infrastruktury. Koszt jest szacunkiem, nie obciążeniem salda.</div>
  </form>
}

function TaskView({ activeTask, events, logsReady, logsComplete, logRef, latestAssistant, cancelSelected, threadRuns, selectTask }) {
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
    <div className={`result-card ${!isActive && report?.success ? 'success' : ''}`}><div className={`result-icon ${!isActive && report?.success ? 'success' : ''}`}>{isActive ? <LoaderCircle className="spin" size={18} /> : report?.success ? <CheckCircle2 size={18} /> : <XCircle size={18} />}</div><div><strong>{isActive ? (activeTask.status === 'queued' ? 'Queued for execution' : 'Agent is working') : report?.success ? 'Task completed and verified' : 'Task finished with issues'}</strong><p>{report?.success ? `SYNVIO verified the task with ${passMatch ? `${passMatch[1]} passing test${passMatch[1] === '1' ? '' : 's'}` : 'a passing ground-truth test run'}${files.length ? ` and recorded ${files.length} changed file${files.length === 1 ? '' : 's'}` : ''}.` : report?.reason || activeTask.error || (isActive ? 'Live progress appears below from the agent event stream.' : 'No report was recorded for this task.')}{failMatch ? ` ${failMatch[1]} failing test${failMatch[1] === '1' ? '' : 's'}.` : ''}</p></div></div>
    {threadRuns.length > 1 && <nav className="thread-runs" aria-label="Etapy tej rozmowy">{threadRuns.map((run,index)=><button key={run.id} type="button" aria-current={run.id===activeTask.id ? 'step' : undefined} onClick={()=>selectTask(run.id)}>{index+1}. {run.task}</button>)}</nav>}
    {activeTask.parent_id && <p className="task-meta">Kontynuacja wcześniejszego zadania · ta sama wersja projektu</p>}
    <ProjectPreview key={activeTask.id} taskId={activeTask.id} revision={`${events.filter((event)=>event.event === 'preview_updated').length}:${activeTask.status}`} />
    {activeTask.download_available && <a className="download-project" href={`/api/tasks/${activeTask.id}/download`}>Pobierz projekt ZIP</a>}
    {report && <div className="metrics-row"><Metric label="Steps" value={Number.isFinite(report.steps_used) && Number.isFinite(limits.global_steps) ? `${report.steps_used} / ${limits.global_steps}` : '—'} /><Metric label="Tool calls" value={Number.isFinite(report.tool_calls) && Number.isFinite(limits.global_tool_calls) ? `${report.tool_calls} / ${limits.global_tool_calls}` : '—'} /><Metric label="Attempts" value={Number.isFinite(report.attempts) && Number.isFinite(limits.max_repair_attempts) ? `${report.attempts} / ${limits.max_repair_attempts}` : '—'} /><Metric label="Duration" value={Number.isFinite(report.duration_seconds) ? `${report.duration_seconds.toFixed(1)}s` : '—'} /></div>}
    {report && <div className="result-details">
      {files.length > 0 && <div className="changed-files"><div className="section-heading"><div><FileText size={15} /><strong>Changed files</strong><span>({files.length})</span></div></div><div className="file-list">{files.map((file) => <FileResult key={`${file.action}:${file.path}`} taskId={activeTask.id} file={file} />)}</div></div>}
      {costRecord && <div className="usage-card"><div className="section-heading"><strong>Koszt modelu · USD</strong></div><div className="usage-values"><Metric label="Budżet" value={`$${costRecord.budget_usd}`} /><Metric label="Szacunek zużycia" value={`$${costRecord.estimated_cost_usd}`} /><Metric label="Koszt do wyjaśnienia" value={`$${costRecord.unresolved_reservation_usd}`} /></div><p className="composer-caption">Bez infrastruktury i podatków. To szacunek, nie obciążenie salda. Brak potwierdzenia zużycia pozostawia rezerwację do wyjaśnienia.</p></div>}
      {tokenValuesAvailable && <div className="usage-card"><div className="section-heading"><div><Activity size={15} /><strong>Model usage</strong></div></div><div className="usage-values">{Number.isFinite(tokens.prompt_tokens) && <Metric label="Input tokens" value={tokens.prompt_tokens.toLocaleString()} />}{Number.isFinite(tokens.completion_tokens) && <Metric label="Output tokens" value={tokens.completion_tokens.toLocaleString()} />}{Number.isFinite(tokens.total_tokens) && <Metric label="Total tokens" value={tokens.total_tokens.toLocaleString()} />}{!costRecord && Number.isFinite(report.estimated_cost_usd) && <Metric label="Szacunek kosztu modelu" value={`$${report.estimated_cost_usd.toFixed(6)}`} />}</div></div>}
    </div>}
    <div className="section-heading"><div><Terminal size={15} /><strong>Execution log</strong><span>({events.length})</span></div><span className="live-label"><span className={isActive ? 'pulse-dot' : ''} />{isActive ? 'Live progress' : logsComplete ? 'Complete' : 'Synchronizing execution log…'}</span></div>
    <div className="event-log" ref={logRef}>{events.length === 0 ? <div className="log-empty"><Clock3 size={16} />Waiting for the first backend event…</div> : events.map((event, index) => <EventRow key={event.sequence || index} event={event} />)}</div>
    {report?.tests?.map((test, index) => <div className="test-result" key={index}><FileText size={15} /><strong>{test.passed ? 'Tests passed' : 'Tests failed'}</strong><span>{test.stdout_tail || test.stderr_tail || 'Allowlisted test runner completed.'}</span>{test.passed ? <Check size={15} /> : <XCircle size={15} />}</div>)}
    {latestAssistant && finalAnswer && <div className="assistant-answer"><Bot size={17} /><div><strong>Agent final response</strong><p>{finalAnswer}</p></div></div>}
  </section>
}

function ProjectPreview({taskId,revision}) {
  const [open,setOpen] = useState(false)
  const [preview,setPreview] = useState(null)
  const [error,setError] = useState('')
  const [device,setDevice] = useState('desktop')
  useEffect(()=>{
    if (!open) return
    let cancelled = false
    setError('')
    getJSON(`/api/tasks/${taskId}/preview`).then((value)=>{if(!cancelled) setPreview(value)}).catch((cause)=>{if(!cancelled) setError(cause.message)})
    return ()=>{cancelled=true}
  },[taskId,revision,open])
  return <section className="project-preview"><div className="preview-toolbar"><button type="button" aria-expanded={open} onClick={()=>setOpen(!open)}>{open ? 'Ukryj podgląd' : 'Podgląd aplikacji'}</button>{open && <select aria-label="Rozmiar podglądu" value={device} onChange={(event)=>setDevice(event.target.value)}><option value="desktop">Komputer</option><option value="mobile">Telefon</option></select>}</div>{open && <><p className="composer-caption">Podgląd zapisanych plików HTML, CSS i JavaScript. Aktualizuje się po zmianach agenta. To podgląd aplikacji, nie obraz przeglądarki agenta.</p>{error ? <p role="alert">{error}</p> : !preview ? <p role="status">Przygotowywanie podglądu…</p> : preview.available ? <div className={`preview-frame ${device}`}><iframe title="Podgląd budowanej aplikacji" sandbox="allow-scripts" referrerPolicy="no-referrer" srcDoc={preview.document}/></div> : <p className="preview-unavailable">{preview.reason}</p>}</>}</section>
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
      const query = new URLSearchParams({ path: file.path })
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
  const tool = event.tool ? <><strong>{event.tool}</strong>{event.allowed === false && <em> blocked</em>}{event.success === false && <em> · failed</em>}{event.output && <pre>{event.output}</pre>}</> : Object.entries(event).filter(([key]) => !['sequence', 'created_at', 'ts', 'event'].includes(key)).map(([key, value]) => <span key={key}>{key}: {typeof value === 'string' ? value : JSON.stringify(value)}</span>)
  return <div className="event-row"><time>{time}</time><span className="event-marker"><span /></span><div className="event-body"><strong className="event-name">{event.event?.replaceAll('_', ' ') || 'event'}</strong>{tool && <div className="event-detail">{tool}</div>}</div></div>
}

export default App