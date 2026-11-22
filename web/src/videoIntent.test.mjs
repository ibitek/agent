import test from 'node:test'
import assert from 'node:assert/strict'
import { requestsVideoRender } from './videoIntent.js'

test('only direct video requests open the storyboard', () => {
  assert.equal(requestsVideoRender('Zrób mi krótki film o kawiarni.'), true)
  assert.equal(requestsVideoRender('Generate a short video about a cafe.'), true)
  assert.equal(requestsVideoRender('Jak działa generator filmów?'), false)
  assert.equal(requestsVideoRender('Zaplanuj film o kawiarni.'), false)
  assert.equal(requestsVideoRender('Build a video player.'), false)
  assert.equal(requestsVideoRender('Create a video editor.'), false)
  assert.equal(requestsVideoRender('Wygeneruj film. Nie generuj go jeszcze.'), false)
})
