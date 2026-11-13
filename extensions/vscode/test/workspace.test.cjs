const test=require('node:test')
const assert=require('node:assert/strict')
const fs=require('node:fs/promises')
const os=require('node:os')
const path=require('node:path')
const workspace=require('../workspace.cjs')

test('paths and known credential files cannot escape the selected project',()=>{
  for(const name of ['../outside','a/../outside','C:\\secrets','/etc/passwd','.env','a/.git/config','a/private.key','credentials.json','node_modules/a.js','CON','src/file.txt:secret','src/file.'])assert.throws(()=>workspace.relativeName(name))
  assert.equal(workspace.relativeName('src/main.js'),'src/main.js')
})
test('read/list exclude links, binary, oversized and hidden files',async()=>{
  const root=await fs.mkdtemp(path.join(os.tmpdir(),'synvio-extension-'))
  try{
    await fs.mkdir(path.join(root,'src'));await fs.writeFile(path.join(root,'src','main.js'),'hello')
    await fs.writeFile(path.join(root,'.env'),'NEVER_UPLOAD')
    await fs.writeFile(path.join(root,'binary.bin'),Buffer.from([0,1,2]))
    await fs.writeFile(path.join(root,'large.txt'),'a'.repeat(16001))
    assert.equal((await workspace.snapshot(root,'src/main.js')).content,'hello')
    assert.equal((await workspace.snapshot(root,'src/new.js',true)).exists,false)
    await assert.rejects(workspace.snapshot(root,'binary.bin'))
    await assert.rejects(workspace.snapshot(root,'large.txt'))
    assert(!(await workspace.list(root)).includes('.env'))
    const elsewhere=await fs.mkdtemp(path.join(os.tmpdir(),'synvio-outside-'))
    try {
      await fs.writeFile(path.join(elsewhere,'secret.txt'),'outside')
      await fs.symlink(elsewhere,path.join(root,'linked'),process.platform==='win32'?'junction':'dir')
      await assert.rejects(workspace.snapshot(root,'linked/secret.txt'))
      await assert.rejects(workspace.resolveFile(root,'linked/new.txt'))
    }finally{await fs.rm(elsewhere,{recursive:true,force:true})}
  }finally{await fs.rm(root,{recursive:true,force:true})}
})
