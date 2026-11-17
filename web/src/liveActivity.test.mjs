import test from 'node:test'
import assert from 'node:assert/strict'
import { activityItems } from './liveActivity.js'

test('reasoning, cost data and unknown payloads never become visible activity', () => {
  assert.deepEqual(activityItems([
    {event:'model_cost', encrypted_content:'private'},
    {event:'reasoning', text:'hidden'}, {event:'unknown', output:'secret'},
    {event:'tool_call', tool:'unknown', arguments:{content:'full source'}},
  ]), [])
})

test('read failures and deletions do not offer a misleading open-file action', () => {
  const items = activityItems([
    {event:'tool_call', tool:'dev_read_file', path:'missing.js', success:false},
    {event:'tool_call', tool:'dev_delete_file', path:'old.js', success:true},
    {event:'tool_call', tool:'dev_write_file', path:'src/App.jsx', success:true, sequence:8},
  ])
  assert.equal(items[0].state, 'error')
  assert.equal(items[0].path, '')
  assert.equal(items[1].path, '')
  assert.equal(items[2].path, 'src/App.jsx')
  assert.equal(items[2].key, 8)
})

test('starting a command does not imply a successful exit or fabricated output', () => {
  const items = activityItems([
    {event:'command_started', command:'npm test'},
    {event:'command_result', command:'npm test', output:'Actual error', exit_code:1},
    {event:'command_result', command:'npm test', exit_code:null},
  ])
  assert.equal(items[0].title, 'Command started')
  assert.equal(items[0].output, '')
  assert.equal(items[1].state, 'error')
  assert.equal(items[1].output, 'Actual error')
  assert.equal(items[2].state, 'info')
  assert.equal(items[2].time, null)
})

test('actual successful checks, screenshots and strategy changes retain their categories', () => {
  const items = activityItems([
    {event:'preview_source_check', success:true, created_at:123},
    {event:'browser_screenshot', text:'Captured by Codex'},
    {event:'strategy_change', text:'Read the source first'},
  ])
  assert.equal(items[0].state, 'success')
  assert.equal(items[0].time, 123)
  assert.equal(items[1].category, 'browser')
  assert.equal(items[2].category, 'checks')
  assert.equal(items[2].detail, 'Read the source first')
})

test('terminal output is bounded and ANSI formatting is removed', () => {
  const [item] = activityItems([{event:'command_result', command:'build', exit_code:0,
    output:'\u001b[31mError\u001b[0m' + 'x'.repeat(14000)}])
  assert.equal(item.output.length, 12000)
  assert.ok(item.output.startsWith('Error'))
  assert.ok(!item.output.includes('\u001b'))
})
