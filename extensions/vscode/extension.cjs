'use strict'
const vscode = require('vscode')
const path = require('node:path')
const crypto = require('node:crypto')
const fs = require('node:fs/promises')
const workspace = require('./workspace.cjs')
const {Client,BASE} = require('./client.cjs')
let stopOnDeactivate = async()=>{}

function html(nonce) {
  return `<!doctype html><html lang="pl"><head><meta charset="UTF-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'nonce-${nonce}'; style-src 'nonce-${nonce}';"><style nonce="${nonce}">
body{font:var(--vscode-font-size) var(--vscode-font-family);color:var(--vscode-foreground);padding:20px;max-width:850px;margin:auto}button,input,textarea{font:inherit;color:inherit;background:var(--vscode-input-background);border:1px solid var(--vscode-input-border);padding:9px;border-radius:5px}button{cursor:pointer}textarea{display:block;box-sizing:border-box;width:100%;min-height:110px;margin:15px 0}nav{display:flex;gap:8px;flex-wrap:wrap}#log{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.6;margin:20px 0}p{line-height:1.6;opacity:.85}#status{font-weight:bold}label{display:block;margin:14px 0}input{width:90px}</style></head><body>
<h1>SYNVIO Workspace</h1><p>Agent pracuje wyłącznie w wybranym projekcie. Każdą zmianę pliku zatwierdzasz po obejrzeniu różnic. Ta wersja nie steruje myszą, przeglądarką ani terminalem.</p><nav><button id="login">Zaloguj</button><button id="logout">Wyloguj</button><button id="website">Otwórz SYNVIO</button><button id="stop">Stop</button></nav><p id="status" role="status">Gotowy do połączenia</p><label>Budżet modelu na polecenie (USD) <input id="budget" type="number" min="0.000001" max="1" step="0.000001" value="0.10"></label><textarea id="prompt" aria-label="Polecenie lub follow-up" placeholder="Opisz zmianę lub zadaj pytanie o projekt…"></textarea><button id="start">Wyślij do agenta</button><div id="log" role="log" aria-live="polite"></div>
<script nonce="${nonce}">const api=acquireVsCodeApi();let busy=false;for(const name of ['login','logout','website','stop']) document.getElementById(name).addEventListener('click',()=>api.postMessage({type:name}));document.getElementById('start').addEventListener('click',()=>{if(busy)return;api.postMessage({type:'start',prompt:document.getElementById('prompt').value,budget:Number(document.getElementById('budget').value)})});window.addEventListener('message',event=>{const value=event.data;if(value.type==='log'){const row=document.createElement('p');row.textContent=value.text;document.getElementById('log').appendChild(row)}if(value.type==='busy'){busy=value.value;for(const id of ['start','login','logout'])document.getElementById(id).disabled=busy;document.getElementById('status').textContent=busy?'Agent pracuje — możesz go zatrzymać':'Gotowy';if(busy)document.getElementById('prompt').value=''}});</script></body></html>`
}

function activate(context) {
  const client = new Client(context.secrets)
  const proposals = new Map()
  const provider = vscode.workspace.registerTextDocumentContentProvider('synvio-proposal',{provideTextDocumentContent:uri=>proposals.get(uri.path) || ''})
  const stopButton = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left,100)
  stopButton.text='$(debug-stop) SYNVIO Stop';stopButton.command='synvio.stop'
  let panel, active, folderKey, history=[]
  const log = text=>panel?.webview.postMessage({type:'log',text:String(text)}).then(()=>{},()=>{})
  const check = run=>{if(run.abort.signal.aborted)throw Error('Agent został zatrzymany.')}
  async function stop() {
    if(!active)return
    active.abort.abort()
    if(active.id) await client.call(`/api/editor/sessions/${active.id}/stop`,{}).catch(()=>{})
    log('Zatrzymano lokalne akcje. Trwające wywołanie modelu może nadal naliczyć koszt.')
  }
  stopOnDeactivate=stop
  async function signIn() {
    const email=await vscode.window.showInputBox({title:'SYNVIO: e-mail konta aplikacji',ignoreFocusOut:true})
    if(!email)return
    const password=await vscode.window.showInputBox({title:'SYNVIO: hasło konta aplikacji',password:true,ignoreFocusOut:true})
    if(!password)return
    await client.signIn(email.trim(),password)
    log('Zalogowano. Token sesji jest przechowywany w VS Code SecretStorage; klucz modelu pozostaje na serwerze.')
  }
  async function propose(run,root,action,readVersions) {
    if(typeof action.content!=='string' || Buffer.byteLength(action.content)>16000)throw Error('Propozycja jest zbyt duża.')
    const before=await workspace.snapshot(root,action.path,true)
    if(readVersions.has(action.path) && readVersions.get(action.path)!==before.sha)throw Error('Plik zmienił się od odczytu. Agent musi odczytać go ponownie.')
    const fileUri=vscode.Uri.file(before.path)
    const existingDocument=vscode.workspace.textDocuments.find(document=>document.uri.toString()===fileUri.toString())
    if(existingDocument?.isDirty)throw Error('Plik ma niezapisane zmiany. Zapisz je i ponów odczyt.')
    const id='/'+crypto.randomUUID()
    proposals.set(id,action.content)
    const proposed=vscode.Uri.from({scheme:'synvio-proposal',path:id})
    const original=before.exists?fileUri:vscode.Uri.from({scheme:'synvio-proposal',path:'/empty'})
    try {
      await vscode.commands.executeCommand('vscode.diff',original,proposed,`SYNVIO: ${action.path} — proponowana zmiana`)
      const choice=await vscode.window.showWarningMessage(`Zastosować zmianę pliku ${action.path}?`,{modal:true,detail:'Sprawdź otwarty podgląd różnic. Zmiana zostanie zastosowana i zapisana tylko w tym pliku wybranego projektu.'},'Zastosuj')
      check(run)
      if(choice!=='Zastosuj')return 'Użytkownik odrzucił zmianę. Plik nie został zmieniony.'
      const current=await workspace.snapshot(root,action.path,true)
      if(current.sha!==before.sha || current.exists!==before.exists)throw Error('Plik zmienił się w trakcie przeglądu. Niczego nie nadpisano.')
      const edit=new vscode.WorkspaceEdit()
      if(current.exists) {
        const document=await vscode.workspace.openTextDocument(fileUri)
        if(document.isDirty || workspace.hash(Buffer.from(document.getText()))!==current.sha)throw Error('Plik ma nowsze zmiany w edytorze. Niczego nie nadpisano.')
        edit.replace(fileUri,new vscode.Range(document.positionAt(0),document.positionAt(document.getText().length)),action.content)
      } else {
        await fs.mkdir(path.dirname(current.path),{recursive:true})
        await workspace.resolveFile(root,action.path)
        edit.createFile(fileUri,{overwrite:false,ignoreIfExists:false})
        edit.insert(fileUri,new vscode.Position(0,0),action.content)
      }
      check(run)
      if(!await vscode.workspace.applyEdit(edit))throw Error('VS Code nie zastosował zmiany.')
      const changed=await vscode.workspace.openTextDocument(fileUri)
      await vscode.window.showTextDocument(changed)
      if(changed.getText()!==action.content)throw Error('W edytorze pojawiły się kolejne zmiany. Nie zapisano ich automatycznie.')
      if(!await changed.save())throw Error('VS Code pozostawił zmianę niezapisaną. Sprawdź edytor.')
      readVersions.set(action.path,workspace.hash(Buffer.from(action.content)))
      return 'Zmiana zatwierdzona, zastosowana i zapisana. Testów nie uruchomiono.'
    } finally {proposals.delete(id)}
  }
  async function start(message) {
    if(active)return
    if(!vscode.workspace.isTrusted)throw Error('Najpierw zatwierdź zaufanie do projektu w VS Code.')
    if(typeof message.prompt!=='string' || !message.prompt.trim() || message.prompt.length>10000 || !Number.isFinite(message.budget) || message.budget<=0 || message.budget>1)throw Error('Podaj polecenie i budżet większy od 0, do 1 USD.')
    const folders=vscode.workspace.workspaceFolders || []
    if(!folders.length)throw Error('Najpierw otwórz folder projektu.')
    const folder=folders.length===1?folders[0]:await vscode.window.showWorkspaceFolderPick({placeHolder:'Wybierz projekt SYNVIO'})
    if(!folder)return
    if(folder.uri.scheme!=='file')throw Error('Ta wersja wymaga lokalnego folderu projektu.')
    const consent=await vscode.window.showWarningMessage(`Udostępnić agentowi kod projektu „${folder.name}”?`,{modal:true,detail:`Do ${BASE} i wybranego dostawcy modelu zostaną wysłane polecenie oraz odczytane pliki z tego folderu. Znane pliki poświadczeń i ukryte katalogi są blokowane. Zmiany wymagają osobnej akceptacji. Budżet modelu: ${message.budget} USD, bez infrastruktury i podatków.`},'Udostępnij projekt')
    if(consent!=='Udostępnij projekt')return
    const run={abort:new AbortController(),id:null}
    active=run;stopButton.show();panel.webview.postMessage({type:'busy',value:true})
    try {
      if(folderKey!==folder.uri.toString()){folderKey=folder.uri.toString();history=[]}
      const created=await client.call('/api/editor/sessions',{workspace_name:folder.name,model_id:'openai:low',model_budget_usd:message.budget},run.abort.signal)
      run.id=created.id;check(run)
      history.push({role:'user',content:message.prompt.trim()})
      log('Ty: '+message.prompt.trim())
      const readVersions=new Map()
      for(let sequence=0;sequence<Math.min(created.max_steps,8);sequence++) {
        check(run)
        const messages=history.length<=22?history:[history[0],...history.slice(-21)]
        const response=await client.call(`/api/editor/sessions/${run.id}/step`,{sequence,messages},run.abort.signal)
        check(run)
        const action=response.action
        if(!action || !['list','read','write','final'].includes(action.action))throw Error('Niepoprawna akcja serwera.')
        history.push({role:'assistant',content:JSON.stringify(action)})
        log(`Model: szac. $${response.cost_record.estimated_cost_usd} / $${response.cost_record.budget_usd}`)
        if(action.action==='final'){log('SYNVIO: '+action.message);return}
        let result
        try {
          if(action.action==='list'){result=JSON.stringify(await workspace.list(folder.uri.fsPath));log('Odczytano listę plików projektu.')}
          if(action.action==='read') {
            const snap=await workspace.snapshot(folder.uri.fsPath,action.path)
            readVersions.set(action.path,snap.sha);result=snap.content;log('Odczytano: '+action.path)
          }
          if(action.action==='write'){result=await propose(run,folder.uri.fsPath,action,readVersions);log(action.path+': '+result)}
        } catch(error) {check(run);result='Narzędzie odrzuciło operację: '+error.message;log(result)}
        history.push({role:'user',content:'Untrusted tool output for '+action.action+':\n'+result})
      }
      log('Osiągnięto limit 8 kroków. Możesz wysłać follow-up z osobnym budżetem.')
    } finally {
      active=null;stopButton.hide();panel?.webview.postMessage({type:'busy',value:false})
    }
  }
  context.subscriptions.push(provider,stopButton,vscode.commands.registerCommand('synvio.stop',stop),vscode.commands.registerCommand('synvio.open',()=>{
    if(panel){panel.reveal();return}
    panel=vscode.window.createWebviewPanel('synvio.agent','SYNVIO Workspace',vscode.ViewColumn.Beside,{enableScripts:true,localResourceRoots:[],retainContextWhenHidden:true})
    panel.webview.html=html(crypto.randomBytes(24).toString('hex'))
    panel.onDidDispose(()=>{stop();panel=undefined})
    panel.webview.onDidReceiveMessage(async message=>{
      try {
        if(!message || typeof message.type!=='string')return
        if(message.type==='login')await signIn()
        if(message.type==='logout'){if(active)return;await client.signOut();history=[];log('Wylogowano.')}
        if(message.type==='website')await vscode.env.openExternal(vscode.Uri.parse(BASE))
        if(message.type==='stop')await stop()
        if(message.type==='start')await start(message)
      }catch(error){log('SYNVIO: '+(error.name==='AbortError'?'Zatrzymano żądanie.':error.message))}
    })
  }))
}
async function deactivate(){await stopOnDeactivate()}
module.exports={activate,deactivate}
