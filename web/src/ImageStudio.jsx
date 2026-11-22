import { useRef, useState } from 'react'
import { ArrowUpRight, Download, ImagePlus, Paperclip, Save, X } from 'lucide-react'
import { getJSON } from './api.js'
import { imageTypes, makeProjectImage, readReferenceImage } from './imageAssets.js'
import './imageStudio.css'

export default function ImageStudio({ taskId, runtime, conversationId, locked, onSave, onOpenFile, onAttach }) {
  const input = useRef(null)
  const reading = useRef(false)
  const [image, setImage] = useState(null)
  const [name, setName] = useState('')
  const [isOver, setIsOver] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [saved, setSaved] = useState(null)
  const nameValid = /^[a-z0-9_-]{1,64}$/i.test(name)
  const directory = runtime === 'app' ? 'public/images' : 'images'

  async function load(file) {
    if (!file || busy || reading.current) return
    reading.current = true
    setError('')
    try {
      const next = await readReferenceImage(file)
      const preview = new Image()
      preview.src = next.url
      await preview.decode().catch(() => { throw new Error('The image could not be opened.') })
      setImage(next)
      setName(file.name.replace(/\.[^.]+$/, '').toLowerCase().replace(/[^a-z0-9_-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 64) || 'reference-image')
      setSaved(null)
    } catch (cause) {
      setError(cause.message)
    } finally {
      reading.current = false
    }
  }

  function paste(event) {
    const item = Array.from(event.clipboardData.items).find(entry => entry.kind === 'file' && entry.type.startsWith('image/'))
    if (!item) return
    event.preventDefault()
    void load(item.getAsFile())
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

  return <section className="image-studio" aria-labelledby="image-studio-title" onPaste={paste}>
    <header className="image-studio-heading">
      <div>
        <h2 id="image-studio-title">Image Studio</h2>
        <p>Preview an image, add it to your project, or attach it to your next message.</p>
      </div>
    </header>
    <input ref={input} hidden type="file" accept={imageTypes.join(',')} onChange={event => {
      const file = event.target.files[0]
      event.target.value = ''
      void load(file)
    }} />
    <div className="image-studio-drop" data-dragging={isOver} onDragOver={event => {
      if (!event.dataTransfer.types.includes('Files')) return
      event.preventDefault()
      setIsOver(true)
    }} onDragLeave={() => setIsOver(false)} onDrop={event => {
      event.preventDefault()
      setIsOver(false)
      void load(event.dataTransfer.files[0])
    }}>
      {image ? <figure>
        <img src={image.url} alt={image.name} />
        <figcaption>{image.name}</figcaption>
      </figure> : <div>
        <span className="image-studio-symbol"><ImagePlus size={24} /></span>
        <h3>Drop or paste an image</h3>
        <p>PNG or JPEG, up to 1 MB</p>
      </div>}
      <button className="image-studio-button" type="button" autoFocus={!image} disabled={busy} onClick={() => input.current.click()}>
        {image ? 'Replace image' : 'Choose image'}
      </button>
    </div>
    {image && <>
      <div className="image-studio-actions">
        <button type="button" className="image-studio-button" disabled={locked || busy} onClick={() => setError(onAttach(image) || '')}>
          <Paperclip size={15} />Attach to chat
        </button>
        <a className="image-studio-button" href={image.url} download={image.name}><Download size={15} />Download original</a>
        <button type="button" className="image-studio-button" disabled={busy} onClick={() => { setImage(null); setSaved(null); setError('') }}>
          <X size={15} />Remove
        </button>
      </div>
      <form className="image-studio-save" onSubmit={save}>
        <label>Project asset name
          <div><span>{directory}/</span><input aria-label="Project asset name" value={name} maxLength={64} disabled={busy}
            onChange={event => { setName(event.target.value); setSaved(null) }} /><span>.svg</span></div>
        </label>
        <button type="submit" className="image-studio-button primary" disabled={!taskId || locked || busy || !nameValid || !!saved}>
          <Save size={15} />{busy ? 'Saving…' : 'Save to project'}
        </button>
        {!taskId ? <p>Open an existing project to save this image to its files.</p> : name && !nameValid ?
          <p>Use letters, numbers, hyphens or underscores in the asset name.</p> :
          <p>The project copy may be resized to fit. The original stays available to download.</p>}
      </form>
    </>}
    {error && <p role="alert" className="work-error">{error}</p>}
    {saved && <div className="image-studio-saved">
      <p role="status">Saved <code>{saved.path}</code>{saved.resized && ' (resized to fit the project file limit)'}.</p>
      <button type="button" className="image-studio-button" onClick={() => onOpenFile(saved.revision, saved.path)}>
        Open in Code &amp; files<ArrowUpRight size={14} />
      </button>
    </div>}
  </section>
}
