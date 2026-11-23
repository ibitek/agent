import assert from 'node:assert/strict'
import test from 'node:test'

test('a shared tab binds to the configured workspace and revokes access across origins', async () => {
  const origin = 'https://workspace.example.test'
  let receive
  let saved
  let targetUrl = 'https://site.example.test/index'
  globalThis.chrome = {
    storage: { session: {
      get: async () => ({ localBrowser: saved }),
      set: async ({ localBrowser }) => { saved = structuredClone(localBrowser) },
      remove: async () => { saved = undefined },
    } },
    action: { setBadgeText: async () => {} },
    runtime: {
      id: 'kaelia-test',
      getManifest: () => ({ host_permissions: [origin + '/*'] }),
      getURL: name => 'chrome-extension://kaelia-test/' + name,
      onMessage: { addListener: handler => { receive = handler } },
    },
    tabs: {
      query: async filter => {
        if (filter.active) return [{ id: 5, url: targetUrl }]
        assert.equal(filter.url, origin + '/*')
        return [{ id: 2, lastAccessed: 1 }, { id: 3, lastAccessed: 2 }]
      },
      get: async () => ({ id: 5, url: targetUrl }),
      onRemoved: { addListener() {} },
      onUpdated: { addListener() {} },
    },
    scripting: { executeScript: async ({ target, args }) => {
      assert.equal(target.tabId, 5)
      assert.equal(args[0], 'inspect')
      return [{ result: { title: 'Fixture', links: [] } }]
    } },
  }
  await import('./worker.js')
  const popup = { id: 'kaelia-test', url: 'chrome-extension://kaelia-test/popup.html' }
  const workspace = { id: 'kaelia-test', origin, frameId: 0, tab: { id: 3 }, documentId: 'owner' }
  const otherTab = { ...workspace, tab: { id: 2 } }
  const otherPage = { ...workspace, documentId: 'foreign' }
  const ask = (sender, operation, sessionId) => new Promise(resolve => {
    assert.equal(receive({ operation, sessionId }, sender, resolve), true)
  })

  assert.equal((await ask(workspace, 'status')).session, null)
  assert.equal((await ask(popup, 'share')).ok, true)
  assert.equal((await ask(otherTab, 'status')).session, null)
  const session = (await ask(workspace, 'status')).session
  assert.equal((await ask(otherPage, 'inspect', session.id)).ok, false)
  assert.equal((await ask(workspace, 'inspect', session.id)).title, 'Fixture')
  targetUrl = 'https://another.example.test/'
  assert.equal((await ask(workspace, 'status')).session, null)
  assert.equal((await ask(workspace, 'inspect', session.id)).ok, false)
})
