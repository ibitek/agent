import { useEffect, useState } from 'react'
import { Box, CheckCircle2, LoaderCircle } from 'lucide-react'
import { getJSON } from './api.js'

export default function CodingEnvironment(){
  const [state,setState]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState('')
  useEffect(()=>{let cancelled=false;getJSON('/api/codex/environment').then(value=>{if(!cancelled)setState(value)}).catch(cause=>{if(!cancelled)setError(cause.message)});return()=>{cancelled=true}},[])
  async function prepare(){setBusy(true);setError('');try{await getJSON('/api/codex/environment/prepare',{method:'POST'});setState(await getJSON('/api/codex/environment'))}catch(cause){setError(cause.message)}finally{setBusy(false)}}
  return <article className="coding-environment"><div className="coding-env-title"><Box size={17}/><strong>Coding &amp; visual review</strong><span>{state?.prepared?'Prepared':'On demand'}</span></div><p>Official Codex CLI, Node.js, Python and Playwright Chromium in an isolated workspace. New frontend projects start with React, Tailwind and shadcn/ui; existing repositories keep their stack.</p><div className="coding-env-tools">{['Terminal','Project files','Desktop + mobile screenshots','Console & overflow checks'].map(tool=><small key={tool}>{tool}</small>)}</div><button disabled={busy||!state||state.prepared} onClick={prepare}>{busy?<><LoaderCircle size={14} className="spin"/>Preparing tools…</>:state?.prepared?<><CheckCircle2 size={14}/>Environment prepared</>:'Prepare coding environment'}</button><p className="composer-caption">Preparation uses cloud compute at an estimated $0.005/minute and may take a few minutes. No model inference. The cached toolchain expires after 24 hours; task environments stop after each run.</p>{error&&<p role="alert" className="work-error">{error}</p>}</article>
}
