const test=require('node:test')
const assert=require('node:assert/strict')
const vm=require('node:vm')
const fs=require('node:fs/promises')
const os=require('node:os')
const path=require('node:path')

async function fixture(t,{decline=false,conflict=false,stop=false}={}) {
  const root=await fs.mkdtemp(path.join(os.tmpdir(),'synvio-approve-'))
  const target=path.join(root,'main.txt');await fs.writeFile(target,'before')
  t.after(()=>fs.rm(root,{recursive:true,force:true}))
  const commands=new Map();let receive,applied=0,afterConsent
  const uri=file=>({scheme:'file',fsPath:file,toString:()=>file})
  const panel={webview:{postMessage:async()=>{},onDidReceiveMessage:callback=>{receive=callback}},onDidDispose:()=>{},reveal:()=>{}}
  const document={uri:uri(target),isDirty:false,getText:()=> 'before',positionAt:value=>value,save:async()=>true}
  const vscode={
    StatusBarAlignment:{Left:1},ViewColumn:{Beside:2},
    Uri:{file:uri,from:value=>({...value,toString:()=>value.path}),parse:value=>value},
    Range:class{},Position:class{},
    WorkspaceEdit:class{replace(){}createFile(){}insert(){}},
    commands:{registerCommand:(name,fn)=>{commands.set(name,fn);return {dispose(){}}},executeCommand:async()=>{}},
    env:{openExternal:async()=>{}},
    workspace:{isTrusted:true,workspaceFolders:[{name:'fixture',uri:uri(root)}],textDocuments:[],registerTextDocumentContentProvider:()=>({dispose(){}}),openTextDocument:async()=>document,applyEdit:async()=>{applied++;return false}},
    window:{createStatusBarItem:()=>({show(){},hide(){},dispose(){}}),createWebviewPanel:()=>panel,showTextDocument:async()=>{},showWarningMessage:async(message)=>{
      if(message.startsWith('Udostępnić'))return 'Udostępnij projekt'
      if(conflict)await fs.writeFile(target,'user changed')
      if(stop)await commands.get('synvio.stop')()
      afterConsent=true
      return decline?undefined:'Zastosuj'
    }}
  }
  class Client {
    async call(url,body) {
      if(url.endsWith('/stop'))return {stopped:true}
      if(!url.endsWith('/step'))return {id:'a'.repeat(32),max_steps:2}
      return {action:body.sequence===0?{action:'write',path:'main.txt',content:'after'}:{action:'final',message:'done'},cost_record:{estimated_cost_usd:'0',budget_usd:'.1'}}
    }
  }
  const module={exports:{}}
  const source=await fs.readFile(path.join(__dirname,'../extension.cjs'),'utf8')
  vm.runInNewContext(source,{module,exports:module.exports,require:name=>name==='vscode'?vscode:name==='./client.cjs'?{Client,BASE:'https://agents.synviocore.xyz'}:name==='./workspace.cjs'?require('../workspace.cjs'):require(name),AbortController,Buffer,console})
  module.exports.activate({secrets:{},subscriptions:[]})
  commands.get('synvio.open')()
  await receive({type:'start',prompt:'change main.txt',budget:.1})
  return {applied,afterConsent,content:await fs.readFile(target,'utf8')}
}
test('declining a diff never applies or saves a file',async t=>{
  const result=await fixture(t,{decline:true});assert.equal(result.applied,0);assert.equal(result.content,'before')
})
test('concurrent user edits are preserved after approval',async t=>{
  const result=await fixture(t,{conflict:true});assert.equal(result.applied,0);assert.equal(result.content,'user changed')
})
test('Stop during approval prevents the approved write',async t=>{
  const result=await fixture(t,{stop:true});assert.equal(result.applied,0);assert.equal(result.content,'before')
})
