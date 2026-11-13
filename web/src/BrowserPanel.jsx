import { useState } from 'react'
import { ExternalLink, Globe, Plus, X } from 'lucide-react'

export default function BrowserPanel(){
  const [tabs,setTabs]=useState([{id:1,url:''}]),[active,setActive]=useState(1),[address,setAddress]=useState(''),[error,setError]=useState('')
  function open(event){
    event.preventDefault();setError('')
    try{
      const url=new URL(/^https?:\/\//i.test(address)?address:`https://${address}`)
      if(!['http:','https:'].includes(url.protocol)||url.username||url.password)throw Error()
      setTabs(previous=>previous.map(tab=>tab.id===active?{...tab,url:url.href}:tab))
      window.open(url.href,'_blank','noopener,noreferrer')
    }catch{setError('Enter an HTTP or HTTPS address without embedded credentials.')}
  }
  return <section className="browser-panel"><div className="browser-tabs">{tabs.map(tab=><button key={tab.id} aria-pressed={tab.id===active} onClick={()=>{setActive(tab.id);setAddress(tab.url)}}><Globe size={13}/>{tab.url?new URL(tab.url).hostname:'New tab'}</button>)}<button aria-label="New browser tab" onClick={()=>{const id=Date.now();setTabs(previous=>[...previous,{id,url:''}]);setActive(id);setAddress('')}}><Plus size={14}/></button></div><form onSubmit={open}><Globe size={14}/><input aria-label="Website address" placeholder="https://example.com" value={address} onChange={event=>setAddress(event.target.value)}/><button type="submit"><ExternalLink size={14}/>Open externally</button></form>{error&&<p role="alert">{error}</p>}<div className="browser-disconnected"><Globe size={34}/><h2>Remote browser not connected</h2><p>Saved addresses open in a separate browser tab. The agent cannot observe or control those pages yet.</p><p>A connected browser session is required for live screenshots, real pointer actions and browser-console output. External pages are not embedded here because many sites block it.</p><small>No browser session or infrastructure is started by opening this panel.</small></div></section>
}
