import { useCallback, useEffect, useRef, useState } from 'react'
import { ArrowDown, ArrowUp, Film, ImagePlus, Plus, Save, Trash2, UsersRound } from 'lucide-react'
import { getJSON } from './api.js'
import { imageTypes, readReferenceImage } from './imageAssets.js'
import VideoRender from './VideoRender.jsx'
import './videoStudio.css'

const sceneLimit = 12
const castLimit = 6
const ratios = { '16:9': 'Landscape', '9:16': 'Portrait', '1:1': 'Square' }
const uuid = () => crypto.randomUUID().replaceAll('-', '')

function newScene(number, direction = '') {
  return { id: uuid(), title: 'Scene ' + number, direction, duration: 5, frame: null, cast: [] }
}

async function fitReference(url) {
  const image = new Image()
  image.src = url
  await image.decode().catch(() => { throw new Error('This image could not be opened.') })
  if (url.length <= 175_000 && Math.max(image.naturalWidth, image.naturalHeight) <= 1600) return url

  const canvas = document.createElement('canvas')
  const context = canvas.getContext('2d')
  if (!context) throw new Error('The browser could not resize this reference.')
  let scale = Math.min(1, 1000 / Math.max(image.naturalWidth, image.naturalHeight))
  for (let attempt = 0; attempt < 7; attempt += 1) {
    canvas.width = Math.max(1, Math.round(image.naturalWidth * scale))
    canvas.height = Math.max(1, Math.round(image.naturalHeight * scale))
    context.fillStyle = '#fff'
    context.fillRect(0, 0, canvas.width, canvas.height)
    context.drawImage(image, 0, 0, canvas.width, canvas.height)
    const resized = canvas.toDataURL('image/jpeg', .82)
    if (resized.length <= 175_000) return resized
    scale *= .76
  }
  throw new Error('This reference could not be resized for the storyboard.')
}

function ReferenceControl({ value, label, onChange }) {
  const [reading, setReading] = useState(false)
  const [error, setError] = useState('')
  async function change(event) {
    const file = event.target.files[0]
    event.target.value = ''
    if (!file) return
    setReading(true)
    setError('')
    try {
      const { url } = await readReferenceImage(file)
      onChange(await fitReference(url))
    } catch (cause) {
      setError(cause.message)
    } finally {
      setReading(false)
    }
  }
  return <div className="video-reference">
    {value && <img src={value} alt={label} />}
    <label className="video-button">
      <ImagePlus size={15} />{reading ? 'Opening…' : value ? 'Replace ' + label.toLowerCase() : 'Add ' + label.toLowerCase()}
      <input type="file" aria-label={label} accept={imageTypes.join(',')} onChange={change} disabled={reading} />
    </label>
    {value && <button className="video-button" type="button" disabled={reading} onClick={() => onChange(null)}>Remove</button>}
    {error && <p role="alert">{error}</p>}
  </div>
}

export default function VideoStudio({ active, boardId, transfer, onTransfer, onDirtyChange }) {
  const endpoint = '/api/video-studio/' + boardId
  const [saved, setSaved] = useState(null)
  const [draft, setDraft] = useState(null)
  const [sceneId, setSceneId] = useState(null)
  const [characterId, setCharacterId] = useState(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const appliedTransfer = useRef(null)
  const flight = useRef(false)
  const dirty = draft !== null && draft !== saved?.storyboard

  const load = useCallback(async (signal) => {
    setLoading(true)
    setError('')
    try {
      const result = await getJSON(endpoint, { signal })
      if (signal?.aborted) return
      setSaved(result)
      setDraft(result.storyboard)
      setSceneId(null)
      setCharacterId(null)
    } catch (cause) {
      if (!signal?.aborted) setError(cause.message)
    } finally {
      if (!signal?.aborted) setLoading(false)
    }
  }, [endpoint])

  useEffect(() => {
    const controller = new AbortController()
    void load(controller.signal)
    return () => controller.abort()
  }, [load])

  useEffect(() => {
    onDirtyChange(dirty)
    if (!dirty) return
    function warn(event) { event.preventDefault(); event.returnValue = '' }
    window.addEventListener('beforeunload', warn)
    return () => {
      onDirtyChange(false)
      window.removeEventListener('beforeunload', warn)
    }
  }, [dirty, onDirtyChange])

  const save = useCallback(async () => {
    if (!dirty || loading || flight.current) return
    flight.current = true
    setSaving(true)
    setError('')
    try {
      const result = await getJSON(endpoint, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ revision: saved.revision, storyboard: draft }),
      })
      setSaved({ revision: result.revision, storyboard: draft })
    } catch (cause) {
      setError(cause.message)
    } finally {
      setSaving(false)
      flight.current = false
    }
  }, [dirty, draft, endpoint, loading, saved])

  useEffect(() => {
    if (!dirty || saving || error) return
    const timer = setTimeout(save, 800)
    return () => clearTimeout(timer)
  }, [dirty, saving, error, save])

  useEffect(() => {
    if (!draft || loading || !transfer || transfer.boardId !== boardId || appliedTransfer.current === transfer.id) return
    appliedTransfer.current = transfer.id
    if (draft.scenes.length >= sceneLimit) {
      onTransfer(transfer, false)
      return
    }
    const scene = newScene(draft.scenes.length + 1, transfer.text)
    setDraft(previous => ({ ...previous, scenes: [...previous.scenes, scene] }))
    setSceneId(scene.id)
    if (transfer.frame) {
      fitReference(transfer.frame).then(frame => {
        setDraft(previous => previous && ({
          ...previous, scenes: previous.scenes.map(item => item.id === scene.id && !item.frame ? { ...item, frame } : item),
        }))
      }).catch(cause => setError(cause.message))
    }
    onTransfer(transfer, true)
  }, [boardId, draft, loading, transfer, onTransfer])

  if (loading) return <p className="video-studio" role="status">Opening storyboard…</p>
  if (!draft) return <section className="video-studio">
    <p role="alert">{error}</p><button type="button" className="video-button" onClick={() => load()}>Try again</button>
  </section>

  const scene = draft.scenes.find(item => item.id === sceneId) || draft.scenes[0]
  const character = draft.characters.find(item => item.id === characterId) || draft.characters[0]
  const duration = draft.scenes.reduce((seconds, item) => seconds + item.duration, 0)
  const sceneNumber = draft.scenes.indexOf(scene) + 1
  const patchScene = (id, changes) => setDraft(previous => ({
    ...previous, scenes: previous.scenes.map(item => item.id === id ? { ...item, ...changes } : item),
  }))
  const patchCharacter = (id, changes) => setDraft(previous => ({
    ...previous, characters: previous.characters.map(item => item.id === id ? { ...item, ...changes } : item),
  }))

  function moveScene(id, step) {
    setDraft(previous => {
      const scenes = [...previous.scenes]
      const from = scenes.findIndex(item => item.id === id), to = from + step
      if (from < 0 || to < 0 || to >= scenes.length) return previous
      const moved = scenes.splice(from, 1)[0]
      scenes.splice(to, 0, moved)
      return { ...previous, scenes }
    })
  }

  return <section className="video-studio" aria-labelledby="video-title">
    <header className="video-heading">
      <div><h2 id="video-title">Video Studio</h2><p>{draft.scenes.length} {draft.scenes.length === 1 ? 'scene' : 'scenes'} · {duration} sec</p></div>
      <div className="video-save">
        <span role="status">{saving ? 'Saving…' : dirty ? 'Unsaved changes' : 'Saved'}</span>
        <button type="button" className="video-button primary" disabled={!dirty || saving} onClick={save}><Save size={15} />Save storyboard</button>
      </div>
    </header>
    {error && <div className="video-error"><p role="alert">{error}</p>
      <button type="button" className="video-button" disabled={saving} onClick={() => load()}>Discard edits and reload</button>
    </div>}
    <div className="video-layout">
      <div className="video-editor">
        <label className="video-field">Storyboard title
          <input value={draft.title} placeholder="Untitled storyboard" maxLength={100}
            onChange={event => setDraft({ ...draft, title: event.target.value })} />
        </label>
        <section className="video-card" aria-label="Storyboard scenes">
          <div className="video-card-heading"><h3>Scenes</h3>
            <button type="button" className="video-button" disabled={draft.scenes.length >= sceneLimit} onClick={() => {
              const next = newScene(draft.scenes.length + 1)
              setDraft({ ...draft, scenes: [...draft.scenes, next] })
              setSceneId(next.id)
            }}><Plus size={14} />Add scene</button>
          </div>
          {scene && <>
            <ol className="video-scene-list">
              {draft.scenes.map((item, index) => <li key={item.id}>
                <button type="button" aria-pressed={scene.id === item.id} className="video-scene-select" onClick={() => setSceneId(item.id)}>
                  <span>{index + 1}</span><strong>{item.title || 'Untitled scene'}</strong><small>{item.duration}s</small>
                </button>
              </li>)}
            </ol>
            <div className="video-card-heading">
              <h4>Scene {sceneNumber}</h4><div className="video-inline">
                <button className="video-button" type="button" aria-label="Move scene earlier" disabled={sceneNumber === 1} onClick={() => moveScene(scene.id, -1)}><ArrowUp size={14} /></button>
                <button className="video-button" type="button" aria-label="Move scene later" disabled={sceneNumber === draft.scenes.length} onClick={() => moveScene(scene.id, 1)}><ArrowDown size={14} /></button>
                <button className="video-button" type="button" aria-label="Remove scene" onClick={() => {
                  setDraft({ ...draft, scenes: draft.scenes.filter(item => item.id !== scene.id) })
                  setSceneId(null)
                }}><Trash2 size={14} /></button>
              </div>
            </div>
            <label className="video-field">Scene name<input value={scene.title} maxLength={100} onChange={event => patchScene(scene.id, { title: event.target.value })} /></label>
            <label className="video-field">Scene direction<textarea value={scene.direction} rows={4} maxLength={3500}
              placeholder="Describe the action, setting, lighting and camera movement."
              onChange={event => patchScene(scene.id, { direction: event.target.value })} /></label>
            <label className="video-field">Duration
              <select value={scene.duration} onChange={event => patchScene(scene.id, { duration: Number(event.target.value) })}>
                {Array.from({length:30}, (_, index) => index + 1).map(seconds => <option key={seconds} value={seconds}>{seconds} sec</option>)}
              </select>
            </label>
            {draft.characters.length > 0 && <fieldset className="video-scene-cast"><legend>Characters in scene</legend>
              {draft.characters.map(item => <label key={item.id}>
                <input type="checkbox" checked={scene.cast.includes(item.id)} onChange={event => patchScene(scene.id, {
                  cast: event.target.checked ? [...scene.cast, item.id] : scene.cast.filter(id => id !== item.id),
                })} />{item.name || 'Unnamed character'}
              </label>)}
            </fieldset>}
            <ReferenceControl key={scene.id} value={scene.frame} label="Frame reference" onChange={frame => patchScene(scene.id, { frame })} />
          </>}
        </section>
        <section className="video-card" aria-label="Character continuity">
          <div className="video-card-heading"><h3><UsersRound size={16} />Characters</h3>
            <button className="video-button" type="button" disabled={draft.characters.length >= castLimit} onClick={() => {
              const next = { id: uuid(), name: 'Character ' + (draft.characters.length + 1), appearance: '', wardrobe: '', reference: null }
              setDraft({ ...draft, characters: [...draft.characters, next] })
              setCharacterId(next.id)
            }}><Plus size={14} />Add</button>
          </div>
          {character && <>
            <label className="video-field">Character
              <select value={character.id} onChange={event => setCharacterId(event.target.value)}>
                {draft.characters.map(item => <option key={item.id} value={item.id}>{item.name || 'Unnamed character'}</option>)}
              </select>
            </label>
            <label className="video-field">Name<input maxLength={80} value={character.name} onChange={event => patchCharacter(character.id, { name: event.target.value })} /></label>
            <label className="video-field">Appearance<textarea maxLength={800} rows={2} value={character.appearance}
              onChange={event => patchCharacter(character.id, { appearance: event.target.value })} /></label>
            <label className="video-field">Wardrobe<textarea maxLength={800} rows={2} value={character.wardrobe}
              onChange={event => patchCharacter(character.id, { wardrobe: event.target.value })} /></label>
            <ReferenceControl key={character.id} value={character.reference} label="Character reference" onChange={reference => patchCharacter(character.id, { reference })} />
            <button className="video-button" type="button" onClick={() => {
              setDraft({ ...draft, characters: draft.characters.filter(item => item.id !== character.id),
                scenes: draft.scenes.map(item => ({ ...item, cast: item.cast.filter(id => id !== character.id) })) })
              setCharacterId(null)
            }}><Trash2 size={14} />Remove character</button>
          </>}
        </section>
      </div>
      <section className="video-stage" aria-label="Storyboard frame preview">
        <header className="video-card-heading"><div><h3>Frame preview</h3>
          <p>{scene ? (scene.title || 'Untitled scene') + ' · ' + scene.duration + ' sec' : 'Select a scene'}</p></div>
          <label className="video-field">Aspect ratio
            <select value={draft.ratio} onChange={event => setDraft({ ...draft, ratio: event.target.value })}>
              {Object.entries(ratios).map(([ratio, name]) => <option key={ratio} value={ratio}>{ratio} · {name}</option>)}
            </select>
          </label>
        </header>
        {scene ? <VideoRender key={scene.id} active={active} boardId={boardId} scene={scene} ratio={draft.ratio}
          revision={saved.revision} dirty={dirty || saving} hasPrevious={sceneNumber > 1} /> :
          <div className="video-frame" data-ratio={draft.ratio}>
            <div className="video-frame-empty"><Film size={28} /><span>Add a scene to start your storyboard</span></div>
          </div>}
        {scene?.cast.length > 0 && <ul className="video-cast-preview" aria-label="Scene character references">
          {draft.characters.filter(item => scene.cast.includes(item.id)).map(item => <li key={item.id}>
            {item.reference ? <img src={item.reference} alt="" /> : <UsersRound size={18} />}
            <div><strong>{item.name || 'Unnamed character'}</strong>{item.wardrobe && <p>{item.wardrobe}</p>}</div>
          </li>)}
        </ul>}
      </section>
    </div>
  </section>
}
