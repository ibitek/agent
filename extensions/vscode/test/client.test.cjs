const test=require('node:test')
const assert=require('node:assert/strict')
const {Client,BASE}=require('../client.cjs')
test('login stores tokens outside workspace and scopes bearer to editor endpoints',async()=>{
  const values=new Map(), calls=[]
  const secrets={get:async key=>values.get(key),store:async(k,v)=>values.set(k,v),delete:async key=>values.delete(key)}
  const client=new Client(secrets,async(url,options={})=>{
    calls.push({url,options})
    const data=url.endsWith('/config')?{supabase_url:'https://project.supabase.co',publishable_key:'public-only'}:url.includes('/auth/v1/token')?{access_token:'fake-access',refresh_token:'fake-refresh',expires_in:3600}:{id:'a'.repeat(32)}
    return {ok:true,json:async()=>data}
  })
  await client.signIn('owner@example.test','test-only-password')
  assert.equal(JSON.parse(values.get('synvio.session')).access,'fake-access')
  await client.call('/api/editor/sessions',{workspace_name:'project'})
  assert.equal(calls.at(-1).options.headers.Authorization,'Bearer fake-access')
  assert.equal(calls.at(-1).url,BASE+'/api/editor/sessions')
  assert.equal(calls.at(-1).options.redirect,'error')
  await assert.rejects(client.call('/api/tasks',{}))
  await assert.rejects(client.call('https://evil.test',{}))
  assert(!calls.some(call=>call.url.includes('evil.test')))
})
test('untrusted auth endpoints are rejected before transmitting password',async()=>{
  let calls=0
  const client=new Client({},async()=>{calls++;return {ok:true,json:async()=>({supabase_url:'https://evil.test',publishable_key:'x'})}})
  await assert.rejects(client.signIn('owner@example.test','fake'))
  assert.equal(calls,1)
})
