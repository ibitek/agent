import { useState } from 'react'
import { Infinity as InfinityIcon, ShieldCheck } from 'lucide-react'
import { formatSpendCap, saveSpendCap } from './spendCap.js'

const OPTIONS = ['0.10', '0.20', '0.50', '1.00', '2.00', 'custom', 'unlimited']
export default function SpendControl({ value, onChange }) {
  const preset = OPTIONS.findIndex(item => item !== 'custom' && (item === value || (item !== 'unlimited' && value !== 'unlimited' && Number(item) === Number(value))))
  const [customMode, setCustomMode] = useState(preset === -1)
  const [custom, setCustom] = useState(preset === -1 ? value : '3.00')
  const index = customMode ? 5 : Math.max(0, preset)
  function commit(next) { if (saveSpendCap(next)) onChange(next) }
  function choose(position) {
    const next = OPTIONS[position]
    setCustomMode(next === 'custom')
    if (next === 'custom') commit(custom)
    else commit(next)
  }
  return <section className="spend-control" aria-labelledby="spend-title">
    <div className="spend-heading"><div><span className="settings-eyebrow"><ShieldCheck size={13}/> YOUR CONTROL</span><h3 id="spend-title">Model spending limit</h3><p>A ceiling for each request, not a fixed charge.</p></div><output className="spend-value" aria-live="polite">{value === 'unlimited' && <InfinityIcon size={20}/>} {formatSpendCap(value)}</output></div>
    <div className="spend-track"><input aria-label="Model spending limit" aria-valuetext={customMode ? `Custom: ${formatSpendCap(value)}` : formatSpendCap(value)} type="range" min="0" max="6" step="1" value={index} onChange={event => choose(Number(event.target.value))} style={{'--spend-progress':`${index / 6 * 100}%`}}/><div className="spend-stops">{OPTIONS.map((option, i) => <button key={option} type="button" aria-pressed={index === i} onClick={() => choose(i)}>{option === 'custom' ? 'Custom' : option === 'unlimited' ? 'Unlimited' : `$${Number(option).toFixed(option === '0.10' || option === '0.20' || option === '0.50' ? 2 : 0)}`}</button>)}</div></div>
    {customMode && <label className="custom-spend">Custom limit <span><b>$</b><input aria-label="Custom spending limit in USD" type="number" min="0.001" step="any" value={custom} onChange={event => {setCustom(event.target.value); commit(event.target.value)}}/></span></label>}
    <div className={`spend-explainer ${value === 'unlimited' ? 'is-unlimited' : ''}`}><ShieldCheck size={15}/><p>{value === 'unlimited' ? 'Unlimited removes the model cost cap. Usage is still metered; time and operation limits remain active.' : 'The service model price is included. Unused allowance is never charged.'} Cloud compute is billed separately.</p></div>
  </section>
}
