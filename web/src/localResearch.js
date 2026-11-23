import { getJSON } from './api.js'

export const researchSteps = 5

async function readPage(send, expected, signal) {
  for (let attempt = 0; attempt < 22; attempt += 1) {
    signal.throwIfAborted()
    const page = await send('read', { expected })
    if (!page.waiting) return page
    await new Promise(resolve => setTimeout(resolve, 450))
  }
  throw new Error('The shared page did not finish loading at the selected address.')
}

export async function researchTab({ send, run, signal, onAction }) {
  while (run.requests < researchSteps) {
    const page = await readPage(send, run.destination, signal)
    signal.throwIfAborted()
    run.destination = null
    const { address, title, text, links } = page
    onAction('Read ' + (title || address))
    run.requests += 1
    const result = await getJSON('/api/local-browser/next', {
      signal,
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        request_id: crypto.randomUUID().replaceAll('-', ''),
        goal: run.goal,
        notes: run.notes,
        page: { address, title, text, links },
        can_follow: run.requests < researchSteps,
      }),
    })
    signal.throwIfAborted()
    run.cost += Number(result.cost)
    if (!run.sources.some(source => source.address === address)) run.sources.push({ address, title })
    run.notes = [...run.notes, (address + ' ' + text).slice(0, 400)].slice(-4)
    if (result.decision.action !== 'follow') return result.decision
    const selected = links.find(link => link.index === result.decision.index)
    if (!selected) throw new Error('The selected link is no longer available.')
    const { destination } = await send('follow', { selection: selected.index, snapshot: page.snapshot })
    run.destination = destination
    onAction('Opened ' + selected.label)
  }
  return { action: 'answer', text: 'The research reached its step limit. Start a new request to continue.' }
}
