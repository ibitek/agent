import { useEffect, useRef, useState } from 'react'
import { Bot, Check, ChevronDown, Moon, Sun } from 'lucide-react'

export function ThemeSwitch(){
  const [theme,setTheme]=useState(()=>document.documentElement.dataset.theme||'dark')
  function toggle(){
    const next=theme==='dark'?'light':'dark'
    document.documentElement.dataset.theme=next
    document.documentElement.style.colorScheme=next
    try{localStorage.setItem('synvio-theme-v1',next)}catch{/* Private browsing can disable storage. */}
    setTheme(next)
  }
  return <button className="theme-switch icon-button" onClick={toggle} aria-label={`Switch to ${theme==='dark'?'light':'dark'} theme`} title={`Switch to ${theme==='dark'?'light':'dark'} theme`}>{theme==='dark'?<Sun size={17}/>:<Moon size={17}/>}</button>
}

export function ModelPicker({models,value,onChange,disabled}){
  const ref=useRef(null)
  const selected=models.find(model=>model.id===value)
  useEffect(()=>{
    const close=event=>{if(!ref.current?.contains(event.target))ref.current?.removeAttribute('open')}
    document.addEventListener('pointerdown',close)
    return ()=>document.removeEventListener('pointerdown',close)
  },[])
  return <details className="model-picker" ref={ref} onKeyDown={event=>{if(event.key==='Escape'){ref.current.removeAttribute('open');ref.current.querySelector('summary').focus()}}}><summary aria-label="Choose AI model"><Bot size={15}/><span>{selected?.label||'gpt-4o-mini'}</span><ChevronDown size={13}/></summary><div className="model-menu"><header><strong>Choose your intelligence</strong><small>Same tools. Your choice of model.</small></header><div className="model-options">{models.map(model=><button key={model.id} type="button" aria-label={`Select ${model.label}`} aria-pressed={value===model.id} disabled={disabled||!model.available} onClick={()=>{onChange(model.id);ref.current.removeAttribute('open');ref.current.querySelector('summary').focus()}}><span className="model-glyph"><Bot size={17}/></span><span className="model-option-copy"><strong>{model.label}</strong><small>{model.description||model.unavailable_reason||'OpenAI'}</small>{model.pricing&&<span>${model.pricing.input_per_million} in · ${model.pricing.cached_input_per_million} cached · ${model.pricing.output_per_million} out / 1M tokens</span>}</span>{value===model.id&&<Check size={16}/>}</button>)}</div><footer>Model cost is estimated. Your message budget still applies.</footer></div></details>
}
