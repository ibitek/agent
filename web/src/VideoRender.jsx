import { useEffect, useRef, useState } from 'react'
import { ArrowUpRight, Film, Play, RefreshCw } from 'lucide-react'
import { getJSON } from './api.js'

const activeStates = ['starting', 'pending', 'unconfirmed']
const pollingStates = ['starting', 'pending']
const currency = new Intl.NumberFormat('en-US', {
  style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 6,
})
const asUSD = value => currency.format(Number(value))

export default function VideoRender({ active, boardId, scene, ratio, revision, dirty, hasPrevious }) {
  const endpoint = '/api/video-studio/' + boardId + '/scenes/' + scene.id + '/render'
  const [catalog, setCatalog] = useState(null)
  const [modelId, setModelId] = useState('')
  const [resolution, setResolution] = useState('480p')
  const [includePrevious, setIncludePrevious] = useState(true)
  const [render, setRender] = useState(null)
  const [statusLoaded, setStatusLoaded] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const [statusError, setStatusError] = useState('')
  const [pollVersion, setPollVersion] = useState(0)
  const [catalogVersion, setCatalogVersion] = useState(0)
  const pendingRequest = useRef(null)

  useEffect(() => {
    if (!active) return
    const controller = new AbortController()
    getJSON('/api/video-renderers', { signal: controller.signal }).then(result => {
      if (!controller.signal.aborted) {
        setCatalog(result)
        setModelId(value => result.models.some(model => model.id === value) ? value : result.models[0]?.id || '')
      }
    }).catch(cause => {
      if (!controller.signal.aborted) setError(cause.message)
    })
    return () => controller.abort()
  }, [active, catalogVersion])

  useEffect(() => {
    if (!active) return
    const controller = new AbortController()
    let timer
    async function check() {
      if (document.hidden) {
        timer = setTimeout(check, 12000)
        return
      }
      let continuePolling = true
      try {
        const result = await getJSON(endpoint, { signal: controller.signal })
        if (controller.signal.aborted) return
        setRender(result.render)
        setStatusLoaded(true)
        setStatusError(result.warning || '')
        continuePolling = pollingStates.includes(result.render?.status)
      } catch (cause) {
        if (!controller.signal.aborted) setStatusError(cause.message)
      }
      if (!controller.signal.aborted && continuePolling) timer = setTimeout(check, 10000)
    }
    void check()
    return () => { controller.abort(); clearTimeout(timer) }
  }, [active, endpoint, pollVersion])

  const model = catalog?.models.find(item => item.id === modelId)
  const supported = model?.ratios.includes(ratio) && model.durations.includes(scene.duration)
  const price = Number(model?.rates[resolution] || 0) * scene.duration
  const activeRender = activeStates.includes(render?.status)

  async function submit(event) {
    event.preventDefault()
    if (!model || !supported || !price || dirty || submitting || activeRender || !statusLoaded || !catalog.connected) return
    setSubmitting(true)
    setError('')
    const options = { revision, model: model.id, resolution, estimate_usd: price.toFixed(6), include_previous: includePrevious }
    const key = JSON.stringify(options)
    if (pendingRequest.current?.key !== key) pendingRequest.current = { key, id: crypto.randomUUID().replaceAll('-', '') }
    try {
      const result = await getJSON(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...options, request_id: pendingRequest.current.id }),
      })
      setRender(result)
      pendingRequest.current = null
    } catch (cause) {
      setError(cause.message)
    } finally {
      setSubmitting(false)
      setPollVersion(value => value + 1)
    }
  }

  async function resume() {
    if (submitting || !render?.can_resume) return
    setSubmitting(true)
    setError('')
    try {
      const result = await getJSON(endpoint + '/resume/' + render.id, { method: 'POST' })
      setRender(result)
    } catch (cause) {
      setError(cause.message)
    } finally {
      setSubmitting(false)
      setPollVersion(value => value + 1)
    }
  }

  return <>
    <div className="video-frame" data-ratio={render?.video_url ? render.ratio : ratio}>
      {render?.video_url ? <video key={render.id} src={render.video_url} controls playsInline preload="metadata"
        aria-label={'Rendered video for ' + (scene.title || 'this scene')} /> :
        scene.frame ? <img src={scene.frame} alt={'Frame reference for ' + (scene.title || 'this scene')} /> :
          <div className="video-frame-empty"><Film size={28} /><span>Add a frame reference or render from the scene description</span></div>}
    </div>
    <form className="video-render-controls" onSubmit={submit}>
      <div className="video-card-heading">
        <h3>Render this scene</h3>
        <button type="button" className="video-button" aria-label="Refresh render status and prices" disabled={submitting}
          onClick={() => { setError(''); setPollVersion(value => value + 1); setCatalogVersion(value => value + 1) }}><RefreshCw size={14} /></button>
      </div>
      {model && <div className="video-render-settings">
        <label className="video-field">Model
          <select value={modelId} disabled={submitting || activeRender} onChange={event => setModelId(event.target.value)}>
            {catalog.models.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}
          </select>
        </label>
        <label className="video-field">Resolution
          <select value={resolution} disabled={submitting || activeRender} onChange={event => setResolution(event.target.value)}>
            {Object.keys(model.rates).map(item => <option key={item} value={item}>{item}</option>)}
          </select>
        </label>
      </div>}
      {hasPrevious && <label className="video-continuity">
        <input type="checkbox" checked={includePrevious} disabled={submitting || activeRender}
          onChange={event => setIncludePrevious(event.target.checked)} />Include previous scene description
      </label>}
      {model && !supported && <p>This model needs a supported ratio and duration.</p>}
      {catalog && !catalog.connected && <p>Connect Vercel AI Gateway to render video.</p>}
      {dirty && <p>Save the storyboard to render its current version.</p>}
      {render && <div className="video-render-status" role="status">
        <strong>{{starting:'Submitting render…', pending:'Rendering in the background', completed:'Render ready',
          failed:'Render failed', unconfirmed:'Submission unconfirmed'}[render.status]}</strong>
        <span>{render.duration}s · {render.resolution} · Estimated {asUSD(render.estimate_usd)}</span>
        {render.error && <p>{render.error}</p>}
        {render.can_resume && <button type="button" className="video-button" disabled={submitting || !catalog?.connected} onClick={resume}>
          <RefreshCw size={14} />Resume same render
        </button>}
        {render.video_url && <a className="video-button" href={render.video_url} target="_blank" rel="noreferrer">
          Open hosted MP4<ArrowUpRight size={14} />
        </a>}
      </div>}
      {statusError && <p role="alert">{statusError}</p>}
      {error && <p role="alert">{error}</p>}
      <button type="submit" className="video-button primary"
        disabled={!catalog?.connected || !supported || !price || !scene.direction.trim() || dirty || submitting || activeRender || !statusLoaded}>
        <Play size={14} />{submitting ? 'Submitting…' : price ? 'Render · est. ' + asUSD(price) : 'Render scene'}
      </button>
    </form>
  </>
}
