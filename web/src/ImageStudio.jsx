import { useRef, useState } from 'react'
import { ArrowUpRight, Download, ImagePlus, Paperclip, Save, Sparkles, X } from 'lucide-react'
import { getJSON } from './api.js'
import { imageTypes, makeProjectImage, prepareMaskedEdit, readReferenceImage } from './imageAssets.js'
import ImageRegions, { describeImageEdits } from './ImageRegions.jsx'
import GeneratedImages from './GeneratedImages.jsx'
import './imageStudio.css'

const maxDrafts = 4

function sourceName(file) {
  return file.name.replace(/\.[^.]+$/, '').toLowerCase().replace(/[^a-z0-9_-]+/g, '-')
    .replace(/^-+|-+$/g, '').slice(0, 64) || 'reference-image'
}

export default function ImageStudio({ taskId, runtime, conversationId, locked, onSave, onOpenFile, onOpenLive,
  onAttach, draft, onDraftChange, onGenerate, generatedImages, onEditGeneratedImage }) {
  const input = useRef(null)
  const reading = useRef(false)
  const { items = [], activeId } = draft || {}
  const current = items.find(item => item.id === activeId)
  const { image, name = '', notes = '', regions = [] } = current || {}
  const [isOver, setIsOver] = useState(false)
  const [busy, setBusy] = useState(false)
  const [loadingImages, setLoadingImages] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [generationPrompt, setGenerationPrompt] = useState('')
  const [size, setSize] = useState('1024x1024')
  const [variants, setVariants] = useState(1)
  const [quality, setQuality] = useState('medium')
  const [saveToProject, setSaveToProject] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [saved, setSaved] = useState(null)
  const nameValid = /^[a-z0-9_-]{1,64}$/i.test(name)
  const directory = runtime === 'app' ? 'public/images' : 'images'
  const editBrief = describeImageEdits(notes, regions)
  const working = busy || loadingImages || generating

  function update(changes) {
    onDraftChange({ items: items.map(item => item.id === activeId ? { ...item, ...changes } : item), activeId })
  }

  function select(nextItems, nextId) {
    onDraftChange({ items: nextItems, activeId: nextId })
    setSaved(null)
    setNotice('')
    setError('')
  }

  async function load(files) {
    if (!files.length || working || reading.current) return
    if (files.length + items.length > maxDrafts) {
      setError('Keep up to four image drafts in this Studio.')
      return
    }
    reading.current = true
    setLoadingImages(true)
    setError('')
    try {
      const incoming = await Promise.all(files.map(async file => {
        const image = await readReferenceImage(file)
        const preview = new Image()
        preview.src = image.url
        await preview.decode().catch(() => { throw new Error('Could not open ' + image.name + '.') })
        return { id: crypto.randomUUID(), image, name: sourceName(file), notes: '', regions: [] }
      }))
      select([...items, ...incoming], incoming[0].id)
    } catch (cause) {
      setError(cause.message)
    } finally {
      reading.current = false
      setLoadingImages(false)
    }
  }

  function paste(event) {
    const files = Array.from(event.clipboardData.items).filter(entry => entry.kind === 'file' && entry.type.startsWith('image/'))
      .map(entry => entry.getAsFile()).filter(Boolean)
    if (!files.length) return
    event.preventDefault()
    void load(files)
  }

  async function save(event) {
    event.preventDefault()
    if (!taskId || !image || !nameValid || locked || busy || reading.current) return
    setBusy(true)
    setError('')
    const path = directory + '/' + name + '.svg'
    const endpoint = '/api/tasks/' + taskId + '/project-files'
    try {
      const { version, read_only: readOnly } = await getJSON(endpoint)
      if (readOnly) throw new Error('Wait for the agent to finish before saving an image.')
      const { content, resized } = await makeProjectImage(image.url)
      const result = await getJSON(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          request_id: crypto.randomUUID().replaceAll('-', ''),
          expected_version: version,
          operation: 'create',
          file: path,
          content,
          conversation_id: conversationId,
        }),
      })
      setSaved({ path, resized, revision: result.id })
      onSave(result.id)
    } catch (cause) {
      setError(cause.message)
    } finally {
      setBusy(false)
    }
  }

  async function renderImages(event) {
    event.preventDefault()
    const prompt = image ? editBrief : generationPrompt.trim()
    if (!prompt || locked || working || reading.current) return
    setGenerating(true)
    setError('')
    try {
      const source = image ? await prepareMaskedEdit(image.url, regions) : null
      if (source && [source.image, source.mask].some(value => value && value.length > 1_398_200)) {
        throw new Error('The source image is too large to send after preparing the selection.')
      }
      const result = await onGenerate(prompt, source ? [source.image] : [],
        { variants, size, quality, mask: source?.mask, save_to_project: saveToProject && Boolean(taskId) })
      if (result !== true) setError(typeof result === 'string' ? result : 'Could not start this image request.')
    } catch (cause) {
      setError(cause.message)
    } finally {
      setGenerating(false)
    }
  }

  return <section className="image-studio" aria-labelledby="image-studio-title" onPaste={paste}>
    <header className="image-studio-heading">
      <div>
        <h2 id="image-studio-title">Image Studio</h2>
        <p>Preview an image, add it to your project, or attach it to your next message.</p>
      </div>
    </header>
    <input ref={input} hidden type="file" accept={imageTypes.join(',')} multiple onChange={event => {
      const files = Array.from(event.target.files)
      event.target.value = ''
      void load(files)
    }} />
    <div className="image-studio-drop" data-loaded={!!image} data-dragging={isOver} onDragOver={event => {
      if (!event.dataTransfer.types.includes('Files')) return
      event.preventDefault()
      setIsOver(true)
    }} onDragLeave={() => setIsOver(false)} onDrop={event => {
      event.preventDefault()
      setIsOver(false)
      void load(Array.from(event.dataTransfer.files))
    }}>
      {!image && <div>
        <span className="image-studio-symbol"><ImagePlus size={24} /></span>
        <h3>Drop or paste an image</h3>
        <p>PNG or JPEG, up to 1 MB</p>
      </div>}
      <button className="image-studio-button" type="button" autoFocus={!image} disabled={working || items.length >= maxDrafts} onClick={() => input.current.click()}>
        {loadingImages ? 'Opening images…' : image ? 'Add images' : 'Choose images'}
      </button>
    </div>
    {image && <>
      <div className="image-studio-drafts" role="group" aria-label="Image drafts">
        {items.map((item, index) => <button className="image-studio-button" key={item.id} type="button"
          disabled={working} aria-pressed={item.id === activeId} aria-label={'Draft ' + (index + 1) + ': ' + item.name}
          onClick={() => select(items, item.id)}>
          <img src={item.image.url} alt="" /><span>{item.name}</span>
        </button>)}
      </div>
      <ImageRegions key={activeId} image={image} regions={regions} disabled={working} onChange={value => update({ regions: value })} />
      <label className="image-edit-notes">Overall edit
        <textarea rows={3} value={notes} maxLength={1200} disabled={working} placeholder="Describe the overall changes you want."
          onChange={event => update({ notes: event.target.value })} />
      </label>
      <div className="image-studio-actions">
        <button type="button" className="image-studio-button" disabled={locked || working} onClick={() => {
          const issue = onAttach(image, editBrief)
          setError(issue || '')
          setNotice(issue ? '' : editBrief ? 'Image and edit brief added to the chat draft.' : 'Image added to the chat draft.')
        }}>
          <Paperclip size={15} />{editBrief ? 'Add image and brief to chat' : 'Attach to chat'}
        </button>
        <a className="image-studio-button" href={image.url} download={image.name}><Download size={15} />Download original</a>
        <button type="button" className="image-studio-button" disabled={working || items.length >= maxDrafts} onClick={() => {
          const copy = { ...current, id: crypto.randomUUID(), name: name.slice(0, 58) + '-draft' }
          select([...items, copy], copy.id)
        }}>Duplicate draft</button>
        <button type="button" className="image-studio-button" disabled={working} onClick={() => {
          const remaining = items.filter(item => item.id !== activeId)
          select(remaining, remaining[0]?.id)
        }}>
          <X size={15} />Remove
        </button>
      </div>
      <form className="image-studio-save" onSubmit={save}>
        <label>Project asset name
          <div><span>{directory}/</span><input aria-label="Project asset name" value={name} maxLength={64} disabled={working}
            onChange={event => { update({ name: event.target.value }); setSaved(null) }} /><span>.svg</span></div>
        </label>
        <button type="submit" className="image-studio-button primary" disabled={!taskId || locked || working || !nameValid || !!saved}>
          <Save size={15} />{busy ? 'Saving…' : 'Save to project'}
        </button>
        {!taskId ? <p>Open an existing project to save this image to its files.</p> : name && !nameValid ?
          <p>Use letters, numbers, hyphens or underscores in the asset name.</p> :
          <p>The project copy may be resized to fit. The original stays available to download.</p>}
      </form>
    </>}
    <form className="image-studio-render" onSubmit={renderImages}>
      {!image && <label>Describe an image
        <textarea rows={3} value={generationPrompt} maxLength={4000} disabled={working}
          placeholder="Describe the subject, composition, and style." onChange={event=>setGenerationPrompt(event.target.value)} />
      </label>}
      <div className="image-studio-render-controls">
        <label>Format
          <select value={size} disabled={working} onChange={event=>setSize(event.target.value)}>
            <option value="1024x1024">Square</option><option value="1536x1024">Landscape</option><option value="1024x1536">Portrait</option>
          </select>
        </label>
        <label>Quality
          <select value={quality} disabled={working} onChange={event=>setQuality(event.target.value)}>
            <option value="low">Low</option><option value="medium">Medium</option>
          </select>
        </label>
        <label>Variants
          <select value={variants} disabled={working} onChange={event=>setVariants(Number(event.target.value))}>
            <option value="1">1</option><option value="2">2</option><option value="3">3</option>
          </select>
        </label>
        <button className="image-studio-button primary" type="submit"
          disabled={working || locked || !(image ? editBrief : generationPrompt.trim())}>
          <Sparkles size={15} />{generating ? 'Rendering…' : image ? 'Generate edits' : 'Generate images'}
        </button>
      </div>
      {taskId && <label className="image-studio-target">
        <input type="checkbox" checked={saveToProject} disabled={working || locked}
          onChange={event=>setSaveToProject(event.target.checked)} />
        Save generated variants to project
      </label>}
      <p>GPT Image 2.5 Flare · Pay as you go. Usage appears with the conversation result.</p>
    </form>
    {!!generatedImages?.length && <section className="image-studio-results" aria-label="Recent image results">
      <h3>Recent results</h3>
      <GeneratedImages images={generatedImages} onEdit={onEditGeneratedImage} disabled={working || locked} />
    </section>}
    {error && <p role="alert" className="work-error">{error}</p>}
    {notice && <p role="status">{notice}</p>}
    {saved && <div className="image-studio-saved">
      <p role="status">Saved <code>{saved.path}</code>{saved.resized && ' (resized to fit the project file limit)'}.</p>
      <button type="button" className="image-studio-button" onClick={() => onOpenFile(saved.revision, saved.path)}>
        Open in Code &amp; files<ArrowUpRight size={14} />
      </button>
      <button type="button" className="image-studio-button" onClick={() => onOpenLive(saved.revision)}>
        View in Live<ArrowUpRight size={14} />
      </button>
    </div>}
  </section>
}
