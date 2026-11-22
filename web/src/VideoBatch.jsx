import { useState } from 'react'
import { Film } from 'lucide-react'
import { getJSON } from './api.js'

const formatUSD = new Intl.NumberFormat('en-US', {
  style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 9,
})

export default function VideoBatch({ boardId, revision, disabled, onSubmitted }) {
  const endpoint = '/api/video-studio/' + boardId + '/render-batch'
  const [quote, setQuote] = useState(null)
  const [results, setResults] = useState(null)
  const [working, setWorking] = useState(false)
  const [error, setError] = useState('')
  const needsEstimate = !quote || quote.revision !== revision

  async function handleSubmit(event) {
    event.preventDefault()
    if (disabled || working) return
    setError('')
    setWorking(true)
    try {
      if (needsEstimate) {
        setQuote(await getJSON(endpoint))
        setResults(null)
      } else {
        const result = await getJSON(endpoint, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ quote: quote.quote }),
        })
        setResults(result.scenes)
        onSubmitted()
      }
    } catch (cause) {
      setError(cause.message)
    } finally {
      setWorking(false)
    }
  }

  const blocked = results?.some(scene => scene.status === 'blocked')
  return <form className="video-card video-batch" onSubmit={handleSubmit}>
    <div className="video-card-heading">
      <h3>Render storyboard</h3>
      <button className="video-button primary" type="submit"
        disabled={disabled || working || Boolean(results && !blocked && !needsEstimate)}>
        <Film size={15} />{working ? 'Working…' : needsEstimate ? 'Estimate all scenes' :
          results ? 'Retry blocked submissions' : 'Render all · est. ' + formatUSD.format(quote.estimate_usd)}
      </button>
    </div>
    {!needsEstimate && <ol aria-label="Scene estimates">
      {quote.scenes.map((scene, index) => {
        const outcome = results?.find(item => item.id === scene.id)
        return <li key={scene.id}>
          {scene.title || 'Scene ' + (index + 1)} · {scene.label} · {formatUSD.format(scene.estimate_usd)}
          {outcome && <> · {outcome.error || (outcome.status === 'pending' ? 'Submitted' : outcome.status)}</>}
        </li>
      })}
    </ol>}
    {results && <p role="status">Open a scene to see its latest status or play the clip.</p>}
    {!needsEstimate && <button type="button" className="video-button" disabled={working}
      onClick={() => { setQuote(null); setResults(null); setError('') }}>Refresh estimate</button>}
    {error && <p role="alert">{error}</p>}
  </form>
}
