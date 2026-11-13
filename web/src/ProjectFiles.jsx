import Prism from 'prismjs'
import 'prismjs/components/prism-jsx'
import 'prismjs/components/prism-typescript'
import 'prismjs/components/prism-tsx'
import 'prismjs/components/prism-json'
import 'prismjs/components/prism-python'
import { useEffect, useState } from 'react'
import { FileCode2, LoaderCircle } from 'lucide-react'
import { getJSON } from './api.js'

export default function ProjectFiles({taskId,revision}){
  const [files,setFiles]=useState([]),[path,setPath]=useState(''),[content,setContent]=useState(''),[error,setError]=useState(''),[loading,setLoading]=useState(false)
  useEffect(()=>{let cancelled=false;getJSON(`/api/tasks/${taskId}/files`).then(value=>{if(!cancelled){const names=value.items.filter(item=>typeof item==='string'||item.exists!==false).map(item=>typeof item==='string'?item:item.path);setFiles(names);setPath(previous=>names.includes(previous)?previous:names[0]||'')}}).catch(cause=>{if(!cancelled)setError(cause.message)});return()=>{cancelled=true}},[taskId,revision])
  useEffect(()=>{if(!path)return;let cancelled=false;setLoading(true);setError('');getJSON(`/api/tasks/${taskId}/files/content?${new URLSearchParams({file:path})}`).then(value=>{if(!cancelled)setContent(value.content)}).catch(cause=>{if(!cancelled)setError(cause.message)}).finally(()=>{if(!cancelled)setLoading(false)});return()=>{cancelled=true}},[taskId,path,revision])
  const language=({html:'markup',css:'css',js:'javascript',jsx:'jsx',ts:'typescript',tsx:'tsx',json:'json',py:'python'})[path.split('.').at(-1)]||'plain'
  const tokens=Prism.tokenize(content,Prism.languages[language]||{})
  const renderTokens=(items)=>items.map((token,index)=>typeof token==='string'?token:<span className={`token ${token.type}`} key={index}>{Array.isArray(token.content)?renderTokens(token.content):typeof token.content==='string'?token.content:renderTokens([token.content])}</span>)
  return <div className="project-files"><nav aria-label="Project files"><strong>CHANGED FILES</strong>{files.map(name=><button key={name} aria-current={path===name?'page':undefined} onClick={()=>setPath(name)}><FileCode2 size={14}/>{name}</button>)}</nav><section aria-label="Source code"><header>{path||'No saved files yet'}<span>Read only</span></header>{error?<p role="alert">{error}</p>:loading?<LoaderCircle className="spin" size={18}/>:<pre className="syntax-code"><code>{renderTokens(tokens)}</code></pre>}</section></div>
}
