import { useEffect, useState } from 'react'
import { Cloud, Monitor } from 'lucide-react'
import LocalBrowser from './LocalBrowser.jsx'

export default function BrowserWorkspace({ children, request, onRequestOpened }) {
  const [view, setView] = useState(request ? 'local' : 'cloud')
  const [prefill, setPrefill] = useState(null)
  useEffect(() => {
    if (!request) return
    setView('local')
    setPrefill(request)
    onRequestOpened()
  }, [request, onRequestOpened])
  return <div className="browser-workspace">
    <div className="browser-tabs" aria-label="Browser location">
      <button type="button" aria-pressed={view === 'cloud'} onClick={() => setView('cloud')}>
        <Cloud size={14} />Cloud
      </button>
      <button type="button" aria-pressed={view === 'local'} onClick={() => setView('local')}>
        <Monitor size={14} />Local tab
      </button>
    </div>
    <div hidden={view !== 'local'}><LocalBrowser prefill={prefill} /></div>
    {view === 'cloud' && children}
  </div>
}
