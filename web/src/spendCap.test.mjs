import test from 'node:test'
import assert from 'node:assert/strict'
import { readSpendCap, providerBudget, formatSpendCap, saveSpendCap, SPEND_CAP_KEY } from './spendCap.js'

test('new default is twenty cents and old settings do not override it', () => {
  globalThis.localStorage={getItem:key=>key==='synvio-spend-cap-v1'?'0.10':null}
  assert.equal(readSpendCap(),'0.20')
})
test('Unlimited survives storage and reaches the API as explicit null', () => {
  const saved=new Map()
  globalThis.localStorage={getItem:key=>saved.get(key),setItem:(key,value)=>saved.set(key,value)}
  assert.ok(saveSpendCap('unlimited'))
  assert.equal(saved.get(SPEND_CAP_KEY),'unlimited')
  assert.equal(readSpendCap(),'unlimited')
  assert.equal(providerBudget(readSpendCap()),null)
  assert.equal(formatSpendCap('unlimited'),'Unlimited')
})
test('custom and preset amounts retain the service price conversion', () => {
  assert.equal(providerBudget('2.00')*1.3,2)
  assert.ok(saveSpendCap('12.50'))
  assert.equal(formatSpendCap('12.50'),'$12.50')
  for(const value of ['', '0', '-1', 'Infinity', 'NaN']) assert.equal(saveSpendCap(value),false)
})
