import { useEffect, useRef, useState } from 'react'
import { ArrowDown, ArrowUp, Link2, Monitor, Pause, RefreshCw, Square } from 'lucide-react'
import { researchTab, researchSteps } from './localResearch.js'

function send(pairing, operation, args = {}) {
  return new Promise((resolve, reject) => {
    if (!window.chrome?.runtime?.sendMessage) {
      reject(new Error('Open Kaelia in Chrome or Edge with the Kaelia extension installed.'))
      return
    }
    window.chrome.runtime.sendMessage(pairing.extension, { code: pairing.code, operation, ...args }, reply => {
      const deliveryError = window.chrome.runtime.lastError
      if (deliveryError) reject(new Error('Could not reach the extension. Check that it is installed and enabled.'))
      else if (!reply?.ok) reject(new Error(reply?.error || 'The extension did not respond.'))
      else resolve(reply)
    })
  })
}

export default function LocalBrowser({ prefill }) {
  const [pairingCode, setPairingCode] = useState('')
  const [session, setSession] = useState(null)
  const [page, setPage] = useState(null)
  const [activity, setActivity] = useState([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [goal, setGoal] = useState('')
  const [researching, setResearching] = useState(false)
  const [report, setReport] = useState(null)
  const [paused, setPaused] = useState(false)
  const pending = useRef(false)
  const researchController = useRef(null)
  const researchRun = useRef(null)

  useEffect(() => () => researchController.current?.abort(), [])
  useEffect(() => {
    if (!prefill) return
    researchController.current?.abort()
    setGoal(prefill.goal)
    setReport(null)
  }, [prefill])

  useEffect(() => {
    if (!session) return
    const timer = setTimeout(() => {
      researchController.current?.abort()
      setSession(null)
      setPage(null)
      setError('Tab access expired. Share the tab again to continue.')
    }, Math.max(0, session.expires - Date.now()))
    return () => clearTimeout(timer)
  }, [session])

  async function perform(callback) {
    if (pending.current) return
    pending.current = true
    setBusy(true)
    setError('')
    try {
      await callback()
    } catch (cause) {
      setError(cause.message)
      setPage(null)
    } finally {
      pending.current = false
      setBusy(false)
    }
  }

  function record(action) {
    setActivity(previous => [{ action, id: crypto.randomUUID() }, ...previous].slice(0, 12))
  }

  function connect(event) {
    event.preventDefault()
    const match = /^([a-p]{32}):([a-f0-9-]{36})$/i.exec(pairingCode.trim())
    if (!match) {
      setError('Paste the complete pairing code from the Kaelia extension.')
      return
    }
    const pairing = { extension: match[1].toLowerCase(), code: match[2].toLowerCase() }
    void perform(async () => {
      const { origin, expires } = await send(pairing, 'connect')
      setSession({ ...pairing, origin, expires })
      setPairingCode('')
      if (researchRun.current?.destination && new URL(researchRun.current.destination).origin !== origin) {
        researchRun.current.destination = null
      }
      const inspected = await send(pairing, 'inspect')
      setPage(inspected)
      record('Inspected the shared tab')
    })
  }

  function inspect() {
    void perform(async () => {
      setPage(await send(session, 'inspect'))
      record('Inspected the shared tab')
    })
  }

  function scroll(selection) {
    void perform(async () => {
      await send(session, 'scroll', { selection })
      record(selection < 0 ? 'Scrolled up' : 'Scrolled down')
    })
  }

  function follow(link) {
    void perform(async () => {
      await send(session, 'click', { selection: link.index, snapshot: page.snapshot })
      setPage(null)
      record('Clicked: ' + link.label)
    })
  }

  function runResearch(event) {
    event.preventDefault()
    if (!session || busy || !goal.trim() || researchController.current) return
    if (!paused || researchRun.current?.goal !== goal.trim()) {
      researchRun.current = { goal: goal.trim(), requests: 0, notes: [], sources: [], cost: 0, destination: null }
    }
    const run = researchRun.current
    const controller = new AbortController()
    researchController.current = controller
    setResearching(true)
    setPaused(false)
    setError('')
    setReport(null)
    setPage(null)
    void researchTab({
      send: (operation, args) => send(session, operation, args),
      run, signal: controller.signal, onAction: record,
    }).then(result => {
      if (controller.signal.aborted) return
      setReport({ ...result, sources: [...run.sources], cost: run.cost })
      if (result.action === 'request_tab') setPaused(run.requests < researchSteps)
    }).catch(cause => {
      if (!controller.signal.aborted) setError(cause.message)
      setPaused(run.requests < researchSteps)
    }).finally(() => {
      if (researchController.current === controller) researchController.current = null
      setResearching(false)
    })
  }

  function stop() {
    researchController.current?.abort()
    void perform(async () => {
      try {
        await send(session, 'stop')
      } finally {
        setSession(null)
        setPage(null)
      }
    })
  }

  return <section className="browser-panel local-browser" aria-label="Local browser">
    <div className="browser-session-bar">
      <Monitor size={16} /><strong>Local browser</strong>
      <span>{session?.origin || 'No tab connected'}</span>
      {session && <button type="button" disabled={busy} onClick={stop}><Square size={13} />Stop</button>}
    </div>
    {session ? <>
      <div className="browser-tabs">
        <button type="button" disabled={busy || researching} onClick={inspect}><RefreshCw size={14} />Inspect tab</button>
        <button type="button" disabled={busy || researching} onClick={() => scroll(-1)}><ArrowUp size={14} />Scroll up</button>
        <button type="button" disabled={busy || researching} onClick={() => scroll(1)}><ArrowDown size={14} />Scroll down</button>
      </div>
      <p className="browser-cost">Access expires at {new Date(session.expires).toLocaleTimeString()}. Links stay on the shared website.</p>
      <form className="local-research-form" onSubmit={runResearch}>
        <input aria-label="Browser research request" placeholder="What should Kaelia find on this website?"
          maxLength={600} disabled={researching} value={goal} onChange={event => setGoal(event.target.value)} />
        <button type="submit" disabled={busy || researching || !goal.trim()}>{paused && researchRun.current?.goal === goal.trim() ? 'Resume' : 'Research tab'}</button>
        {researching && <button type="button" onClick={() => researchController.current?.abort()}><Pause size={14} />Pause</button>}
      </form>
      <p className="browser-cost">Research sends visible text from the shared tab to the AI provider. Up to 5 model calls, with an estimated cap of $0.026 per request.</p>
      {page && <div className="local-browser-content">
        <h3>{page.title || 'Shared tab'}</h3>
        {page.links.length ? <ul className="local-browser-links">
          {page.links.map(link => <li key={link.index}>
            <button type="button" disabled={busy || researching} onClick={() => follow(link)}><Link2 size={14} />{link.label}</button>
          </li>)}
        </ul> : <p>No eligible links were found on this page.</p>}
      </div>}
    </> : <>
      <p className="browser-cost">In Chrome or Edge, open the website, choose Share this tab in the Kaelia extension, then paste the pairing code. Use /browse in chat to bring a request here.</p>
      <form onSubmit={connect}>
        <input type="password" aria-label="Pairing code" placeholder="Pairing code" autoComplete="off"
          maxLength={69} value={pairingCode} onChange={event => setPairingCode(event.target.value)} />
        <button type="submit" disabled={busy || !pairingCode.trim()}>Connect tab</button>
      </form>
    </>}
    {report && <div className="local-browser-content" role="status">
      <h3>{report.action === 'request_tab' ? 'Another tab is needed' : 'Research result'}</h3>
      <p className="local-research-result">{report.text}</p>
      {report.action === 'request_tab' && <p>Stop sharing this tab, then share and connect the requested website to resume.</p>}
      {!!report.sources.length && <ul aria-label="Pages read">{report.sources.map(source => <li key={source.address}>
        <a href={source.address} target="_blank" rel="noreferrer">{source.title || source.address}</a>
      </li>)}</ul>}
      <p>Estimated AI cost: ${report.cost.toFixed(6)}</p>
    </div>}
    {error && <p role="alert" className="diagnostic-error local-browser-content">{error}</p>}
    {!!activity.length && <div className="local-browser-content">
      <h3>Local activity</h3>
      <ul aria-live="polite">{activity.map(entry => <li key={entry.id}>{entry.action}</li>)}</ul>
    </div>}
  </section>
}
