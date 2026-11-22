import { useRef, useState } from 'react'
import { Crosshair, Trash2 } from 'lucide-react'

const maxRegions = 8
const unit = value => Math.max(0, Math.min(1, value))
const percent = value => Math.round(value * 1000) / 10 + '%'

function point(event) {
  const bounds = event.currentTarget.getBoundingClientRect()
  return {
    x: unit((event.clientX - bounds.left) / bounds.width),
    y: unit((event.clientY - bounds.top) / bounds.height),
  }
}

function rectangle(start, end) {
  return { x: Math.min(start.x, end.x), y: Math.min(start.y, end.y),
    width: Math.abs(start.x - end.x), height: Math.abs(start.y - end.y) }
}

export function describeImageEdits(notes, regions) {
  const changes = regions.map((region, index) => region.note.trim() &&
    'Area ' + (index + 1) + ' (left ' + percent(region.x) + ', top ' + percent(region.y) +
    ', width ' + percent(region.width) + ', height ' + percent(region.height) + '): ' + region.note.trim()
  ).filter(Boolean)
  const instructions = [notes.trim(), ...changes].filter(Boolean)
  if (!instructions.length) return ''
  return 'Image editing brief\n\n' + instructions.join('\n\n') +
    (changes.length ? '\n\nRegion coordinates are relative to the original image. Keep unmarked areas unchanged unless the brief says otherwise.' : '')
}

export default function ImageRegions({ image, regions, onChange, disabled }) {
  const [marking, setMarking] = useState(false)
  const [draft, setDraft] = useState(null)
  const start = useRef(null)
  const canAdd = !disabled && regions.length < maxRegions

  function addRegion(box) {
    onChange([...regions, { ...box, id: crypto.randomUUID(), note: '' }])
    setMarking(false)
  }

  function onPointerDown(event) {
    if (!marking || !canAdd || !event.isPrimary || event.button !== 0) return
    const origin = point(event)
    start.current = origin
    setDraft(rectangle(origin, origin))
    event.currentTarget.setPointerCapture(event.pointerId)
  }

  function onPointerUp(event) {
    if (!start.current || !event.isPrimary) return
    const region = rectangle(start.current, point(event))
    start.current = null
    setDraft(null)
    if (canAdd && region.width >= 0.025 && region.height >= 0.025) addRegion(region)
  }

  function update(id, changes) {
    onChange(regions.map(region => region.id === id ? { ...region, ...changes } : region))
  }

  const renderBox = (region, index) => <span key={region.id || 'draft'} className="image-region-mark"
    style={{ left: percent(region.x), top: percent(region.y), width: percent(region.width), height: percent(region.height) }}>
    {index != null && <i>{index + 1}</i>}
  </span>

  return <div className="image-regions">
    <div className="image-region-canvas" data-marking={marking} onPointerDown={onPointerDown}
      onPointerMove={event => { if (start.current && event.isPrimary) setDraft(rectangle(start.current, point(event))) }}
      onPointerUp={onPointerUp} onPointerCancel={event => { if (event.isPrimary) { start.current = null; setDraft(null) } }}>
      <img src={image.url} alt={image.name} draggable={false} />
      <div className="image-region-overlay" aria-hidden="true">
        {regions.map(renderBox)}
        {draft && renderBox(draft)}
      </div>
    </div>
    <div className="region-toolbar">
      <button type="button" className="image-studio-button" aria-pressed={marking} disabled={!canAdd}
        onClick={() => setMarking(value => !value)}><Crosshair size={15} />{marking ? 'Cancel selection' : 'Mark an area'}</button>
      <button type="button" className="image-studio-button" disabled={!canAdd}
        onClick={() => addRegion({ x: .3, y: .3, width: .4, height: .4 })}>Add box with coordinates</button>
      {marking && <span role="status">Drag across the area to change.</span>}
    </div>
    {regions.length > 0 && <ol className="image-region-notes" aria-label="Marked areas">
      {regions.map((region, index) => <li key={region.id}>
        <div className="region-note-heading"><strong>Area {index + 1}</strong>
          <button type="button" className="image-studio-button" aria-label={'Remove area ' + (index + 1)} disabled={disabled}
            onClick={() => onChange(regions.filter(item => item.id !== region.id))}><Trash2 size={14} /></button>
        </div>
        <textarea aria-label={'Change for area ' + (index + 1)} placeholder="What should change here?" value={region.note}
          maxLength={350} rows={2} disabled={disabled} onChange={event => update(region.id, { note: event.target.value })} />
        <details><summary>Position and size</summary><div className="region-coordinate-controls">
          {[['x', 'Left', 1 - region.width], ['y', 'Top', 1 - region.height],
            ['width', 'Width', 1 - region.x], ['height', 'Height', 1 - region.y]].map(([field, label, max]) =>
            <label key={field}>{label} %
              <input type="number" min={field.length === 1 ? 0 : 2.5} max={Math.round(max * 1000) / 10}
                step="any" value={Math.round(region[field] * 1000) / 10} disabled={disabled}
                onChange={event => {
                  if (event.target.value === '') return
                  const minimum = field.length === 1 ? 0 : .025
                  const amount = event.target.valueAsNumber / 100
                  if (Number.isFinite(amount)) update(region.id, { [field]: Math.min(max, Math.max(minimum, amount)) })
                }} />
            </label>)}
        </div></details>
      </li>)}
    </ol>}
  </div>
}
