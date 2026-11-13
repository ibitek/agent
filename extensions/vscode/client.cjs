'use strict'
const BASE = 'https://agents.synviocore.xyz'
class Client {
  constructor(secrets,fetcher=fetch) {this.secrets=secrets;this.fetcher=fetcher;this.config=null}
  async json(url,options={}) {
    const response = await this.fetcher(url,{...options,redirect:'error',signal:options.signal ? AbortSignal.any([options.signal,AbortSignal.timeout(90000)]) : AbortSignal.timeout(15000)})
    const body = await response.json()
    if (!response.ok) throw Error(response.status===401 ? 'Sesja wygasła. Zaloguj się ponownie.' : body.detail || 'Żądanie nie powiodło się.')
    return body
  }
  async settings() {
    if (!this.config) {
      const value = await this.json(BASE+'/api/editor/config')
      const url = new URL(value.supabase_url)
      if (url.protocol!=='https:' || !url.hostname.endsWith('.supabase.co') || url.pathname!=='/' || url.username || url.password || url.port) throw Error('Niepoprawny adres logowania.')
      this.config={url:url.origin,key:value.publishable_key}
    }
    return this.config
  }
  async signIn(email,password) {
    const config = await this.settings()
    const response = await this.fetcher(config.url+'/auth/v1/token?grant_type=password',{method:'POST',redirect:'error',signal:AbortSignal.timeout(15000),headers:{apikey:config.key,'Content-Type':'application/json'},body:JSON.stringify({email,password})})
    if (!response.ok) throw Error('Niepoprawny e-mail lub hasło. Użyj konta aplikacji SYNVIO.')
    const data = await response.json()
    await this.save(data)
  }
  async save(data) {
    if (!data.access_token || !data.refresh_token) throw Error('Nie udało się utworzyć sesji.')
    await this.secrets.store('synvio.session',JSON.stringify({access:data.access_token,refresh:data.refresh_token,expires:Date.now()+Math.min(data.expires_in || 3600,3600)*1000}))
  }
  async token() {
    const raw = await this.secrets.get('synvio.session')
    if (!raw) throw Error('Najpierw zaloguj się do SYNVIO.')
    const session = JSON.parse(raw)
    if (session.expires > Date.now()+60000) return session.access
    const config = await this.settings()
    const response = await this.fetcher(config.url+'/auth/v1/token?grant_type=refresh_token',{method:'POST',redirect:'error',signal:AbortSignal.timeout(15000),headers:{apikey:config.key,'Content-Type':'application/json'},body:JSON.stringify({refresh_token:session.refresh})})
    if (!response.ok) {await this.secrets.delete('synvio.session');throw Error('Zaloguj się ponownie do SYNVIO.')}
    const data = await response.json();await this.save(data)
    return data.access_token
  }
  async call(path,body,signal) {
    if (!/^\/api\/editor\/sessions(?:\/[a-f0-9]{32}\/(?:step|stop))?$/.test(path)) throw Error('Niedozwolona operacja.')
    return this.json(BASE+path,{method:'POST',headers:{Authorization:'Bearer '+await this.token(),'Content-Type':'application/json'},body:JSON.stringify(body),signal})
  }
  async signOut() {
    try {
      const config = await this.settings()
      const raw = await this.secrets.get('synvio.session')
      if(raw) await this.fetcher(config.url+'/auth/v1/logout?scope=local',{method:'POST',redirect:'error',signal:AbortSignal.timeout(15000),headers:{apikey:config.key,Authorization:'Bearer '+JSON.parse(raw).access}})
    } finally {await this.secrets.delete('synvio.session')}
  }
}
module.exports = {Client,BASE}
