import { useEffect, useRef, useState } from 'react'
import { Github, X } from 'lucide-react'
import { getJSON } from './api.js'

const uuid = () => crypto.randomUUID().replaceAll('-', '')

function ReviewChanges({ projectUrl, changedFiles }) {
  const [file, setFile] = useState(changedFiles[0].path)
  const [result, setResult] = useState(null)

  useEffect(() => {
    let active = true
    getJSON(projectUrl + '/review?file=' + encodeURIComponent(file))
      .then(data => { if (active) setResult({ file, ...data }) })
      .catch(error => { if (active) setResult({ file, error: error.message }) })
    return () => { active = false }
  }, [projectUrl, file])

  const current = result?.file === file ? result : null

  return <div className="github-review">
    <label>Review {changedFiles.length} changed {changedFiles.length === 1 ? 'file' : 'files'}
      <select value={file} onChange={event => setFile(event.target.value)}>
        {changedFiles.map(({ path, action }) => <option key={path} value={path}>{path} ({action})</option>)}
      </select>
    </label>
    <div className="github-diff" aria-live="polite">
      {!current ? <p>Loading file diff…</p> : current.error ? <p role="alert">{current.error}</p> :
        current.diff ? <pre>{current.diff.split('\n').map((line, index) => (
          <span key={index} data-change={line.startsWith('+') ? 'added' : line.startsWith('-') ? 'deleted' : undefined}>{line}{'\n'}</span>
        ))}</pre> : <p>Empty file. See its change type above.</p>}
      {current?.truncated && <p>Diff shortened here. The draft pull request includes the full change.</p>}
    </div>
  </div>
}

export default function GitHubPanel({ taskId, onClose, onImport }) {
  const [config, setConfig] = useState(null)
  const [source, setSource] = useState(null)
  const [review, setReview] = useState(null)
  const [reviewError, setReviewError] = useState('')
  const [repos, setRepos] = useState([])
  const [repository, setRepository] = useState('')
  const [branch, setBranch] = useState('')
  const [privateRepo, setPrivateRepo] = useState(false)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [title, setTitle] = useState('Update application with Kaelia')
  const [published, setPublished] = useState('')
  const dialogRef = useRef(null)
  const importId = useRef(null)
  const publishId = useRef(null)
  const projectUrl = '/api/github/projects/' + taskId

  useEffect(() => { dialogRef.current.showModal() }, [])
  useEffect(() => {
    let active = true
    getJSON('/api/github/status')
      .then(data => { if (active) setConfig(data) })
      .catch(cause => { if (active) setError(cause.message) })
    if (taskId) {
      getJSON(projectUrl).then(async data => {
        if (!active || !data.source) return
        setSource(data.source)
        const changes = await getJSON(projectUrl + '/review')
        if (active) setReview(changes)
      }).catch(cause => { if (active) setReviewError(cause.message) })
    }
    return () => { active = false }
  }, [projectUrl, taskId])

  async function loadRepos() {
    setError('')
    try {
      const data = await getJSON('/api/github/repositories')
      setRepos(data.items)
    } catch (cause) {
      setError(cause.message)
    }
  }

  async function importRepo(event) {
    event.preventDefault()
    setBusy('import')
    setError('')
    importId.current ??= uuid()
    try {
      const data = await getJSON('/api/github/import', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ request_id: importId.current, repository, branch: branch || null, use_connection: privateRepo }),
      })
      await onImport(data.conversation_id)
      onClose()
    } catch (cause) {
      setError(cause.message)
    } finally {
      setBusy('')
    }
  }

  async function publish(event) {
    event.preventDefault()
    if (!review?.files.length || published || busy) return
    setBusy('publish')
    setError('')
    publishId.current ??= uuid()
    try {
      const data = await getJSON(projectUrl + '/pull-request', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ request_id: publishId.current, title: title.trim() }),
      })
      setPublished(data.url)
    } catch (cause) {
      setError(cause.message)
    } finally {
      setBusy('')
    }
  }

  return <dialog ref={dialogRef} className="github-dialog" onCancel={onClose} aria-label="GitHub repositories">
    <header>
      <h2><Github size={21} />GitHub</h2>
      <button onClick={onClose} aria-label="Close GitHub panel"><X size={17} /></button>
    </header>
    {error && <p role="alert" className="work-error">{error}</p>}
    {source && <section className="github-source">
      <h3>{source.repository}</h3>
      <p>Base branch <strong>{source.branch}</strong> · <code>{source.commit_sha.slice(0, 12)}</code> · {source.retained?.length || 0} paths preserved</p>
      <a href={'https://github.com/' + source.repository + '/archive/' + source.commit_sha + '.zip'} target="_blank" rel="noreferrer">Download original repository</a>
      {reviewError ? <p role="alert">{reviewError}</p> : !review ? <p>Preparing change review…</p> :
        review.files.length ? <ReviewChanges projectUrl={projectUrl} changedFiles={review.files} /> :
          <p>This revision matches the imported source.</p>}
      {published ? <a href={published} target="_blank" rel="noreferrer">Open draft pull request</a> :
        !!review?.files.length && <form onSubmit={publish}>
          <label>Pull request title
            <input value={title} onChange={event => { setTitle(event.target.value); publishId.current = null }} maxLength={150} disabled={!!busy} required />
          </label>
          <p>Creates a new branch and opens a draft pull request for review.</p>
          <button disabled={!!busy || !config?.connected || !title.trim()}>{busy === 'publish' ? 'Creating draft…' : 'Create draft pull request'}</button>
        </form>}
    </section>}
    {!source && reviewError && <p role="alert">{reviewError}</p>}
    <section>
      <h3>Import repository</h3>
      <form onSubmit={importRepo}>
        <label>Repository
          <input required placeholder="owner/repository or https://github.com/owner/repository" value={repository}
            onChange={event => { setRepository(event.target.value); importId.current = null }} disabled={!!busy} />
        </label>
        <label>Branch (optional)
          <input placeholder="Default branch" value={branch}
            onChange={event => { setBranch(event.target.value); importId.current = null }} disabled={!!busy} />
        </label>
        <label className="github-checkbox">
          <input type="checkbox" disabled={!config?.connected || !!busy} checked={privateRepo}
            onChange={event => { setPrivateRepo(event.target.checked); importId.current = null }} />
          Use connected GitHub App
        </label>
        {config?.connected ? <>
          <button type="button" onClick={loadRepos} disabled={!!busy}>List connected repositories</button>
          {repos.length > 0 && <select aria-label="Connected repository" value={repos.some(repo => repo.full_name === repository) ? repository : ''} disabled={!!busy}
            onChange={event => { setRepository(event.target.value); setPrivateRepo(true); importId.current = null }}>
            <option value="">Choose a repository</option>
            {repos.map(repo => <option key={repo.full_name}>{repo.full_name}</option>)}
          </select>}
        </> : config && <p>
          Private imports and draft pull requests require a connected GitHub App.
          {config.install_url && <> <a href={config.install_url} target="_blank" rel="noreferrer">Install GitHub App</a></>}
        </p>}
        <p className="github-limits">Imports up to 100 editable UTF-8 files, 2 MiB total and 256 KiB per file. Binary and protected paths stay in GitHub.</p>
        <button disabled={!!busy || !repository.trim()}>{busy === 'import' ? 'Importing…' : 'Import repository'}</button>
      </form>
    </section>
  </dialog>
}
