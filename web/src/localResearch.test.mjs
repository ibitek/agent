import assert from 'node:assert/strict'
import test from 'node:test'
import { localBrowserCommand, researchTab } from './localResearch.js'

test('explicit Polish and English commands are local', () => {
  assert.equal(localBrowserCommand('Pokaż linki.'), 'inspect')
  assert.equal(localBrowserCommand('przewiń w dół'), 'down')
  assert.equal(localBrowserCommand('scroll up'), 'up')
  assert.equal(localBrowserCommand('find a refund policy'), undefined)
})

test('research follows an offered link and reports the pages it actually opened', async context => {
  const origin = 'https://example.test'
  const pages = [
    { address: origin + '/', title: 'Home', text: 'Help for visitors', snapshot: 'first',
      links: [{ index: 0, label: 'Returns' }] },
    { address: origin + '/returns', title: 'Returns', text: 'Returns accepted for 30 days', snapshot: 'second',
      links: [] },
  ]
  const decisions = [{ action: 'follow', index: 0 }, { action: 'answer', text: 'Returns accepted for 30 days.' }]
  const actions = []
  context.mock.method(globalThis, 'fetch', async (url, options) => {
    assert.equal(url, '/api/local-browser/next')
    const data = JSON.parse(options.body)
    assert.match(data.page.address, /^https:\/\/example\.test/)
    assert.equal('snapshot' in data.page, false)
    return new Response(JSON.stringify({ decision: decisions.shift(), cost: '0.001' }))
  })
  const run = { goal: 'Find the return policy', requests: 0, notes: [], sources: [], cost: 0, destination: null }
  async function send(operation, args) {
    if (operation === 'read') {
      if (run.requests === 1) assert.equal(args.expected, origin + '/returns')
      return pages.shift()
    }
    assert.equal(operation, 'follow')
    assert.deepEqual(args, { selection: 0, snapshot: 'first' })
    return { destination: origin + '/returns' }
  }

  const result = await researchTab({ send, run, signal: new AbortController().signal,
    onAction: action => actions.push(action) })
  assert.equal(result.action, 'answer')
  assert.deepEqual(actions, ['Read Home', 'Opened Returns', 'Read Returns'])
  assert.deepEqual(run.sources.map(page => page.address), [origin + '/', origin + '/returns'])
  assert.equal(run.cost, 0.002)
})
