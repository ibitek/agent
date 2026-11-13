'use strict'
const fs = require('node:fs/promises')
const path = require('node:path')
const crypto = require('node:crypto')
const excluded = new Set(['node_modules','venv','dist','build','__pycache__'])
function relativeName(value) {
  if (typeof value !== 'string' || !value || value.length > 200 || value.includes('\0') || path.win32.isAbsolute(value) || path.posix.isAbsolute(value) || /^[a-z]:/i.test(value)) throw Error('Niepoprawna ścieżka.')
  const parts = value.replaceAll('\\','/').split('/')
  if (parts.some(part=>!part || part.startsWith('.') || excluded.has(part.toLowerCase()))) throw Error('Ścieżka poza dozwolonym kodem projektu.')
  if (parts.some(part=>part.includes(':') || /[. ]$/.test(part) || /^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i.test(part))) throw Error('Niejednoznaczna lub zarezerwowana nazwa pliku.')
  if (/(?:\.(?:pem|key|pfx|p12)|(?:^|\/)(?:id_rsa|id_ed25519|credentials\.json|secrets\.json))$/i.test(parts.join('/'))) throw Error('Pliki poświadczeń są zablokowane.')
  return parts.join('/')
}
function inside(root, target) {
  const relative = path.relative(root,target)
  return relative === '' || (!relative.startsWith('..'+path.sep) && relative !== '..' && !path.isAbsolute(relative))
}
async function resolveFile(root, name) {
  const safe = relativeName(name)
  const canonicalRoot = await fs.realpath(root)
  const target = path.resolve(canonicalRoot,safe)
  if (!inside(canonicalRoot,target)) throw Error('Plik poza wybranym projektem.')
  let existing = target
  for (;;) {
    try {
      const stat = await fs.lstat(existing)
      if (stat.isSymbolicLink()) throw Error('Dowiązania symboliczne są niedozwolone.')
      if (!inside(canonicalRoot,await fs.realpath(existing))) throw Error('Ścieżka prowadzi poza projekt.')
      break
    } catch(error) {
      if (error.code !== 'ENOENT') throw error
      const parent = path.dirname(existing)
      if (parent === existing) throw error
      existing = parent
    }
  }
  // Check every existing component, including links resolving inside the project.
  let partPath = canonicalRoot
  for (const part of safe.split('/')) {
    partPath = path.join(partPath,part)
    try { if ((await fs.lstat(partPath)).isSymbolicLink()) throw Error('Dowiązania symboliczne są niedozwolone.') }
    catch(error) { if(error.code !== 'ENOENT') throw error }
  }
  return target
}
function hash(value) {return crypto.createHash('sha256').update(value).digest('hex')}
async function snapshot(root,name,allowMissing=false) {
  const target = await resolveFile(root,name)
  let bytes
  try {
    const stat = await fs.stat(target)
    if (!stat.isFile() || stat.size > 16000) throw Error('Dozwolone są pliki tekstowe do 16 KB.')
    bytes = await fs.readFile(target)
  } catch(error) {
    if (allowMissing && error.code === 'ENOENT') return {path:target,exists:false,content:'',sha:null}
    throw error
  }
  if (bytes.length > 16000 || bytes.includes(0)) throw Error('Plik binarny lub zbyt duży.')
  const content = new TextDecoder('utf-8',{fatal:true}).decode(bytes)
  return {path:target,exists:true,content,sha:hash(bytes)}
}
async function list(root) {
  const result = [], queue = ['']
  let visited = 0
  while(queue.length && result.length < 100 && visited < 1000) {
    const current = queue.shift()
    for (const entry of await fs.readdir(path.join(root,current),{withFileTypes:true})) {
      if (++visited > 1000) break
      const name = current ? current+'/'+entry.name : entry.name
      try {relativeName(name)} catch {continue}
      if (entry.isSymbolicLink()) continue
      if (entry.isDirectory()) queue.push(name)
      else if (entry.isFile()) result.push(name)
      if (result.length >= 100) break
    }
  }
  return result
}
module.exports = {relativeName,resolveFile,snapshot,list,hash}
