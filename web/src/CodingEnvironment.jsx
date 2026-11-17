import { useEffect, useState } from 'react'
import { Box } from 'lucide-react'
import { getJSON } from './api.js'

export default function CodingEnvironment(){
  const [state,setState]=useState(null),[error,setError]=useState('')
  useEffect(()=>{let cancelled=false;getJSON('/api/codex/environment').then(value=>{if(!cancelled)setState(value)}).catch(cause=>{if(!cancelled)setError(cause.message)});return()=>{cancelled=true}},[])
  return <article className="coding-environment"><div className="coding-env-title"><Box size={17}/><strong>Build workspace</strong><span>{state?.prepared?'Ready':'Managed by Kaelia'}</span></div><p>Your coding tools and browser are managed for you. Describe what you want to build; Kaelia opens the workspace with its tools installed.</p><div className="coding-env-tools">{['Project files','Terminal','Application preview','Desktop & mobile review'].map(tool=><small key={tool}>{tool}</small>)}</div><p className="composer-caption">Select a Codex model to use this workspace. Existing projects keep their tools and dependencies. No manual installation is required.</p>{error&&<p role="status">Workspace status is temporarily unavailable.</p>}</article>
}
