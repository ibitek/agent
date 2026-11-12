import test from 'node:test'
import assert from 'node:assert/strict'
import { watchTask, mergeTasks } from './taskPolling.js'

function harness(responses) {
  const queue = [], snapshots = [], requests = []
  let index = 0
  const stop = watchTask('task-a', {
    getJSON: async (url) => { requests.push(url); return responses[index++] },
    onSnapshot: (value) => snapshots.push(value),
    schedule: (fn) => { queue.push(fn); return fn },
    cancel: (fn) => { const i = queue.indexOf(fn); if (i >= 0) queue.splice(i, 1) },
  })
  return { queue, snapshots, requests, stop, tick: async () => queue.shift()() }
}
test('terminal status keeps polling until the final persisted event arrives', async () => {
  const h = harness([
    { id: 'task-a', status: 'failed' }, { items: [{ sequence: 1, event: 'tool_call' }] },
    { id: 'task-a', status: 'failed' }, { items: [{ sequence: 2, event: 'task_finished' }] },
  ])
  await h.tick()
  assert.equal(h.snapshots[0].events.length, 1)
  assert.equal(h.snapshots[0].complete, false)
  await h.tick()
  assert.deepEqual(h.snapshots[1].events.map((e) => e.sequence), [1, 2])
  assert.equal(h.snapshots[1].complete, true)
  assert.equal(h.queue.length, 0)
})
test('old history cannot replace a newer task status', () => {
  const current = { id: 'a', status: 'running', updated_at: 5 }
  assert.equal(mergeTasks([current], [{ id: 'a', status: 'queued', updated_at: 4 }])[0].status, 'running')
  assert.equal(mergeTasks([{ ...current, status: 'failed' }], [current])[0].status, 'failed')
})
test('changing tasks ignores late results from the previous selection', async () => {
  let release, published = false
  const queue = []
  const stop = watchTask('old', {
    getJSON: (url) => url.endsWith('/old') ? new Promise((resolve) => { release = resolve }) : Promise.resolve({items:[]}),
    onSnapshot: () => { published = true }, schedule: (fn) => { queue.push(fn); return fn }, cancel: () => {},
  })
  const pending = queue.shift()()
  stop()
  release({id:'old',status:'failed'})
  await pending
  assert.equal(published, false)
  assert.equal(queue.length, 0)
})
test('all event pages are loaded before the terminal snapshot is published', async () => {
  const h = harness([
    {id:'task-a',status:'completed'},
    {items:Array.from({length:500},(_,i)=>({sequence:i+1,event:'tool_call'}))},
    {items:[{sequence:501,event:'task_finished'}]},
  ])
  await h.tick()
  assert.equal(h.snapshots.length,1)
  assert.equal(h.snapshots[0].events.length,501)
  assert.equal(h.snapshots[0].complete,true)
  assert.match(h.requests[2], /after=500/)
})
test('transient network error never fabricates a failed status', async () => {
  const snapshots=[],queue=[]
  watchTask('a',{getJSON:async()=>{throw Error('offline')},onSnapshot:v=>snapshots.push(v),schedule:fn=>{queue.push(fn);return fn},cancel:()=>{}})
  await queue.shift()()
  assert.equal(snapshots.length,0)
  assert.equal(queue.length,1)
})
