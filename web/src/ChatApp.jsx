import LivePanel from './LivePanel.jsx'
import BrowserPanel from './BrowserPanel.jsx'
import CodexBrowser from './CodexBrowser.jsx'
import GitHubPanel from './GitHubPanel.jsx'
import AccountPanel from './AccountPanel.jsx'
import Diagnostics from './Diagnostics.jsx'
import ProjectFiles from './ProjectFiles.jsx'
import ImageStudio from './ImageStudio.jsx'
import GeneratedImages from './GeneratedImages.jsx'
import { generatedImageUrl, readReferenceImage } from './imageAssets.js'
import { ModelPicker, ThemeSwitch } from './StudioControls.jsx'
import { useCallback, useEffect, useRef, useState } from 'react'
import { Image, ImagePlus, ArrowUp, Activity, Wallet, Bot, Code2, Eye, FileCode2, Maximize2, Minimize2, FolderOpen, LoaderCircle, MessageSquare, PanelLeftClose, Plus, Settings2, ShieldCheck, Sparkles, X } from 'lucide-react'
import { getJSON } from './api.js'
import { mergeTasks, watchTask } from './taskPolling.js'
import { ProjectPreview, TaskView } from './WorkDetails.jsx'
import { EnvironmentPanel, LoginPanel } from './AuthPanels.jsx'
import { LiveProgress, MessageBody, UsageLine, WorkCard } from './ChatBits.jsx'
const uuid=()=>crypto.randomUUID().replaceAll('-','')

export default function ChatApp(){
  const [session,setSession]=useState(null),[health,setHealth]=useState(null),[models,setModels]=useState([])
  const [tasks,setTasks]=useState([]),[history,setHistory]=useState([]),[projects,setProjects]=useState([]),[conversationId,setConversationId]=useState(null),[messages,setMessages]=useState([])
  const [selected,setSelected]=useState(null),[eventState,setEventState]=useState({taskId:null,items:[],complete:false})
  const [images,setImages]=useState([]),imageInput=useRef(null)
  const [studioDrafts,setStudioDrafts]=useState({})
  const studioKey=conversationId||selected||'draft'
  const recentImages=messages.findLast(message=>message.generated_images?.length)?.generated_images
  const [prompt,setPrompt]=useState(''),[modelId,setModelId]=useState('auto')
  const [pendingRequest,setPendingRequest]=useState(null),[pendingUsage,setPendingUsage]=useState(null)
  const [loading,setLoading]=useState(false),[opening,setOpening]=useState(false),[preparing,setPreparing]=useState(false),[error,setError]=useState('')
  const [environments,setEnvironments]=useState(false),[details,setDetails]=useState(true),[tab,setTab]=useState('preview'),[mobile,setMobile]=useState(false)
  const [previewRuntime,setPreviewRuntime]=useState({taskId:null,items:[]}),[previewNonce,setPreviewNonce]=useState(0)
  const [liveFile,setLiveFile]=useState(null)
  const [account,setAccount]=useState(null),[github,setGithub]=useState(false)
  const [collapsed,setCollapsed]=useState(true),[focusPreview,setFocusPreview]=useState(false)
  const bottom=useRef(null),logRef=useRef(null),loadNumber=useRef(0),dirtyFiles=useRef(false),startedRuns=useRef(new Set())
  useEffect(()=>{
    if(!pendingRequest)return
    const controller=new AbortController()
    let timer
    async function poll(){
      try{
        const usage=await getJSON(`/api/assistant/usage/${pendingRequest}`,{signal:controller.signal})
        if(!controller.signal.aborted)setPendingUsage(usage)
      }catch{
        // The reply carries its final estimate even if a polling request fails.
      }
      if(!controller.signal.aborted)timer=setTimeout(poll,1500)
    }
    timer=setTimeout(poll,500)
    return ()=>{controller.abort();clearTimeout(timer)}
  },[pendingRequest])
  const activeTask=tasks.find(task=>task.id===selected),events=eventState.taskId===selected?eventState.items:[]
  useEffect(()=>{if(session && !session.authenticated)setStudioDrafts({})},[session?.authenticated])
  useEffect(()=>{let cancelled=false;getJSON('/api/session').then(value=>{if(!cancelled)setSession(value)}).catch(cause=>{if(!cancelled)setError(cause.message)});const expired=()=>setSession(value=>({...value,authenticated:false}));window.addEventListener('synvio-session-expired',expired);return ()=>{cancelled=true;window.removeEventListener('synvio-session-expired',expired)}},[])
  const refresh=useCallback(async()=>{const [chats,work,saved]=await Promise.all([getJSON('/api/conversations'),getJSON('/api/tasks'),getJSON('/api/projects')]);setHistory(chats.items);setProjects(saved.items);setTasks(previous=>mergeTasks(previous,work.items))},[])
  const open=useCallback(async value=>{if(dirtyFiles.current){setError('Save or discard your file edits before changing projects.');return;}const request=++loadNumber.current;setOpening(true);setError('');setMobile(false);setCollapsed(true);try{const data=await getJSON(`/api/conversations/${value}`);if(request!==loadNumber.current)return;setConversationId(value);setMessages(data.messages);setSelected(data.last_task_id||null);setDetails(true);setTab('preview');window.history.replaceState(null,'',`?conversation=${value}`)}catch(cause){if(request===loadNumber.current)setError(cause.message)}finally{if(request===loadNumber.current)setOpening(false)}},[])
  useEffect(()=>{if(!session?.authenticated)return;let cancelled=false;Promise.allSettled([getJSON('/api/health'),getJSON('/api/models'),refresh()]).then(([h,m])=>{if(cancelled)return;if(h.status==='fulfilled')setHealth(h.value);if(m.status==='fulfilled')setModels(m.value.items);if(h.status==='rejected')setError(h.reason.message)});const value=new URLSearchParams(window.location.search).get('conversation');if(value&&/^[a-f0-9]{32}$/.test(value))open(value);return ()=>{cancelled=true}},[session?.authenticated,refresh,open])
  useEffect(()=>{if(!selected||!session?.authenticated)return;return watchTask(selected,{getJSON,onSnapshot:({task,events:items,complete})=>{if(task.status==='queued'&&!startedRuns.current.has(task.id)){startedRuns.current.add(task.id);getJSON(`/api/tasks/${task.id}/run`,{method:'POST'}).catch(cause=>setError(cause.message))}setTasks(previous=>mergeTasks(previous,[task]));setEventState({taskId:selected,items,complete})}})},[selected,session?.authenticated,previewNonce])
  useEffect(()=>{bottom.current?.scrollIntoView({behavior:window.matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth',block:'end'})},[messages.length,loading])
  function fresh(){if(loading)return;if(dirtyFiles.current){setError('Save or discard your file edits first.');return;}setCollapsed(true);setFocusPreview(false);loadNumber.current++;setOpening(false);setConversationId(null);setMessages([]);setSelected(null);setPrompt('');setImages([]);setError('');setDetails(false);setMobile(false);window.history.replaceState(null,'',window.location.pathname)}
  async function addImages(files){
    if(loading)return;
    if(images.length+files.length>2){setError('Attach at most two images.');return}
    try{const added=await Promise.all(files.map(readReferenceImage));setImages(previous=>[...previous,...added].slice(0,2))}catch(cause){setError(cause.message)}
  }
  function attachImages(event){const files=Array.from(event.target.files||[]);event.target.value='';addImages(files)}
  function pasteImages(event){const files=Array.from(event.clipboardData?.items||[]).filter(item=>item.kind==='file'&&item.type.startsWith('image/')).map(item=>item.getAsFile()).filter(Boolean);if(files.length){event.preventDefault();addImages(files)}}
  function submit(event){
    event.preventDefault()
    return sendMessage(prompt.trim()||'Analyze the attached image.',images.map(image=>image.url))
  }
  function moveStudioDraft(newId){
    setStudioDrafts(previous=>{
      if(!previous[studioKey])return previous
      const entries=Object.entries(previous).filter(([key])=>key!==studioKey&&key!==newId)
      return Object.fromEntries([...entries,[newId,previous[studioKey]]].slice(-4))
    })
  }
  async function sendMessage(text, attachments, imageOptions){
    if(dirtyFiles.current){setError('Save or discard your file edits before starting another request.');return false}
    if(loading||opening||(!imageOptions&&!prompt.trim()&&!attachments.length))return false
    const requestId=uuid(),currentId=conversationId
    setPendingUsage(null)
    setPendingRequest(requestId)
    setLoading(true)
    setError('')
    if(!imageOptions)setPrompt('')
    setMessages(previous=>[...previous,{id:requestId,role:'user',content:text,image_count:attachments.length}])
    try{
      const result=await getJSON('/api/assistant',{
        method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({request_id:requestId,conversation_id:currentId,
          parent_task_id:!currentId?selected:null,message:text,images:attachments,
          model_id:modelId,model_budget_usd:null,image_options:imageOptions}),
      })
      if(!imageOptions)setImages([])
      if(!currentId)moveStudioDraft(result.conversation_id)
      setConversationId(result.conversation_id)
      window.history.replaceState(null,'','?conversation='+result.conversation_id)
      setMessages(previous=>[...previous,result.message])
      if(result.message.project_images?.length){
        setSelected(result.message.task_id)
        setDetails(true)
        setTab('live')
      }
      if(result.message.kind==='build'){
        setSelected(result.message.task_id)
        setDetails(true)
        setTab('preview')
        setPreviewNonce(value=>value+1)
      }
      if(result.task){
        setSelected(result.task.id)
        setTasks(previous=>mergeTasks(previous,[{...result.task,task:text,created_at:Date.now()/1000,updated_at:0}]))
        setDetails(true)
        setTab('live')
        getJSON('/api/tasks/'+result.task.id+'/run',{method:'POST'}).catch(cause=>setError(cause.message))
      }
      refresh().catch(()=>{})
      return true
    }catch(cause){
      const usage=await getJSON('/api/assistant/usage/'+requestId).catch(()=>null)
      setMessages(previous=>[...previous,{id:requestId+'-error',role:'assistant',kind:'error',
        content:cause.message,cost_record:usage?.service_estimated_cost_usd!=null?usage:null}])
      if(!currentId)moveStudioDraft(requestId)
      if(imageOptions)setError(cause.message)
      setConversationId(currentId||requestId)
      window.history.replaceState(null,'','?conversation='+(currentId||requestId))
      refresh().catch(()=>{})
      return cause.message
    }finally{
      setPendingRequest(null)
      setLoading(false)
    }
  }
  async function prepare(){setPreparing(true);setError('');try{await getJSON('/api/setup/sandbox',{method:'POST'});setHealth(await getJSON('/api/health'))}catch(cause){setError(cause.message)}finally{setPreparing(false)}}
  async function pauseWork(id){
    const task=await getJSON(`/api/tasks/${id}/pause`,{method:'POST'})
    setTasks(previous=>mergeTasks(previous,[task]))
  }
  async function cancelSelected(){try{await getJSON(`/api/tasks/${selected}/cancel`,{method:'POST'});await refresh()}catch(cause){setError(cause.message)}}
  async function resumeWork(value,ack){const result=await getJSON(`/api/tasks/${value}/resume`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({request_id:uuid(),conversation_id:conversationId,model_budget_usd:null,acknowledge_unknown_cost:ack})});await refresh();setSelected(result.task.id);setDetails(true);setTab('live');if(conversationId){const chat=await getJSON(`/api/conversations/${conversationId}`);setMessages(chat.messages)}getJSON(`/api/tasks/${result.task.id}/run`,{method:'POST'}).catch(cause=>setError(cause.message))}
  async function adoptRevision(value){await refresh();setSelected(value);if(conversationId){const chat=await getJSON(`/api/conversations/${conversationId}`);setMessages(chat.messages)}}
  function changeWorkspaceTab(next){if(dirtyFiles.current&&next!=='code'){setError('Save or discard your file edits before leaving Code & files.');return;}setTab(next)}
  function openStudio(){
    if(dirtyFiles.current){setError('Save or discard your file edits before opening Image Studio.');return}
    setDetails(true)
    setTab('studio')
    if(window.matchMedia('(max-width: 620px)').matches)setFocusPreview(true)
  }
  async function editGeneratedImage(asset){
    if(dirtyFiles.current){setError('Save or discard your file edits before opening Image Studio.');return}
    if((studioDrafts[studioKey]?.items.length||0)>=4){setError('Remove an image draft in Studio first.');openStudio();return}
    const navigation=loadNumber.current
    try{
      const response=await fetch(generatedImageUrl(asset.id))
      if(response.status===401)window.dispatchEvent(new Event('synvio-session-expired'))
      if(!response.ok)throw new Error('This image is no longer available.')
      const image=await readReferenceImage(new File([await response.blob()],asset.name,{type:'image/jpeg'}))
      if(navigation!==loadNumber.current)return
      const item={id:uuid(),image,name:asset.name.replace(/\.jpg$/i,''),notes:'',regions:[]}
      setStudioDrafts(previous=>{
        const items=previous[studioKey]?.items||[]
        if(items.length>=4)return previous
        return Object.fromEntries([...Object.entries(previous).filter(([key])=>key!==studioKey),[studioKey,{items:[...items,item],activeId:item.id}]].slice(-4))
      })
      openStudio()
    }catch(cause){if(navigation===loadNumber.current)setError(cause.message)}
  }
  function attachStudioImage(image, brief){
    const existing=images.findIndex(item=>item.url===image.url)
    if(existing>=0&&!brief)return 'This image is already attached.'
    if(existing<0&&images.length>=2)return 'Remove one of the two chat attachments first.'
    if(brief){
      const attachment=existing>=0?existing+1:images.length+1
      const addition='For attached image '+attachment+': '+brief
      const next=prompt.trimEnd()?[prompt.trimEnd(),addition].join('\n\n'):addition
      if(next.length>10000)return 'Shorten the existing chat draft before adding the edit brief.'
      setPrompt(next)
    }
    if(existing<0)setImages(previous=>[...previous,image].slice(0,2))
    setFocusPreview(false)
  }
  async function adoptStudioRevision(value, projectTask, projectConversation){
    setSelected(current=>current===projectTask?value:current)
    const navigation=loadNumber.current
    try{
      await refresh()
      if(projectConversation){
        const chat=await getJSON('/api/conversations/'+projectConversation)
        if(loadNumber.current===navigation)setMessages(chat.messages)
      }
    }catch(cause){if(loadNumber.current===navigation)setError(cause.message)}
  }
  function openLiveFile(path){if(dirtyFiles.current){setError('Save or discard your file edits first.');return;}setLiveFile({taskId:selected,path});setTab('code')}
  function showTask(value,view='preview'){if(dirtyFiles.current){setError('Save or discard your file edits first.');return;}setSelected(value);setDetails(true);setTab(view);if(window.matchMedia('(max-width: 620px)').matches)setFocusPreview(true)}
  if(!session)return <main className="login-page"><div className="login-card"><div className="brand-mark"><img src="/kaelia.svg" width="32" height="32" alt="" /></div><h1>Kaelia</h1><p role="status">{error||'Connecting to your workspace…'}</p></div></main>
  if(!session.authenticated)return <LoginPanel session={session} onLogin={setSession}/>
  const legacy=tasks.filter(task=>!history.some(chat=>chat.last_task_id===task.id)).slice(0,8)
  const threadRuns=activeTask?tasks.filter(task=>(task.thread_id||task.id)===(activeTask.thread_id||activeTask.id)).sort((a,b)=>a.created_at-b.created_at):[]
  return <div className={`app-shell unified-shell builder-layout ${focusPreview?'preview-focused':''} ${mobile?'sidebar-is-open':''} ${collapsed?'sidebar-collapsed':''}`}>
  {(!collapsed||mobile)&&<button className="history-scrim" aria-label="Close conversation history" onClick={()=>{setCollapsed(true);setMobile(false)}}/>}{github&&<GitHubPanel key={selected} taskId={selected} onClose={()=>setGithub(false)} onImport={async value=>{await refresh();await open(value)}}/>}{account&&<AccountPanel onModelsChanged={async()=>{const value=await getJSON('/api/models');setModels(value.items)}} initialTab={account} {...{models,modelId,setModelId}} onClose={()=>setAccount(null)}/>}{environments&&<EnvironmentPanel health={health} onClose={()=>setEnvironments(false)}/>}
  <aside className="sidebar"><div className="brand"><div className="brand-mark"><img src="/kaelia.svg" width="32" height="32" alt="" /></div><strong>Kaelia</strong><span className="brand-beta">WORKSPACE</span><button className="icon-button sidebar-toggle" onClick={()=>window.matchMedia('(min-width: 851px)').matches?setCollapsed(true):setMobile(false)} aria-label="Close sidebar"><PanelLeftClose size={17}/></button></div><button className="new-task" onClick={fresh} disabled={loading}><Plus size={16}/>New conversation</button><div className="side-label">YOUR CONVERSATIONS <span>{history.length}</span></div><nav className="history-list" aria-label="Conversation history">{history.length===0&&<div className="empty-history">A fresh space for your next idea.</div>}{history.map(item=><button disabled={loading} key={item.id} className={`history-item ${conversationId===item.id?'selected':''}`} onClick={()=>open(item.id)}><MessageSquare size={14}/><span className="history-copy"><span>{item.title}</span><small>{new Date(item.updated_at*1000).toLocaleDateString('en',{month:'short',day:'numeric'})}</small></span></button>)}</nav>
  {projects.length>0&&<><div className="side-label">SAVED PROJECTS</div><nav className="history-list" aria-label="Saved projects">{projects.map(project=><button className="history-item" key={project.id} disabled={loading} onClick={async()=>{if(dirtyFiles.current){setError('Save or discard your file edits first.');return;}try{const data=await getJSON(`/api/projects/${project.id}/open`,{method:'POST'});await refresh();await open(data.conversation_id)}catch(cause){setError(cause.message)}}}><FolderOpen size={14}/><span className="history-copy"><span>{project.title}</span></span></button>)}</nav></>}
  {legacy.length>0&&<><div className="side-label">PREVIOUS WORK</div><nav className="history-list" aria-label="Previous work">{legacy.map(task=><button key={task.id} className="history-item" disabled={loading} onClick={()=>{loadNumber.current++;setOpening(false);setConversationId(null);setMessages([{id:task.id,role:'user',content:task.task},{id:task.id+'-work',role:'assistant',content:'Your saved work is available below.',task_id:task.id}]);setSelected(task.id);setDetails(true);setTab('preview');setCollapsed(true);setMobile(false)}}><FolderOpen size={14}/><span className="history-copy"><span>{task.task}</span></span></button>)}</nav></>}
  <div className="sidebar-bottom"><div className="local-badge"><ShieldCheck size={15}/><span>Private workspace</span><span className="online-dot"/></div><button className="environment-link" onClick={()=>setEnvironments(true)}><Settings2 size={15}/>Environments</button></div></aside>
  <main className="chat-main"><header className="topbar"><div className="topbar-title"><button className="icon-button mobile-menu" onClick={()=>window.matchMedia('(min-width: 851px)').matches?setCollapsed(!collapsed):setMobile(!mobile)} aria-label="Toggle sidebar"><PanelLeftClose size={18}/></button><span>Kaelia</span><span className="topbar-subtitle">Your ideas, in motion.</span></div><div className="top-actions"><button className="wallet-launch" onClick={()=>setAccount('wallet')}><Wallet size={14}/>Wallet</button><button className="wallet-launch" onClick={()=>setGithub(true)}>GitHub</button><ThemeSwitch/><span className={`connection ${health?.status==='ok'?'connected':''}`}><span/>{health?.status==='ok'?'Connected':'Connecting'}</span>{selected&&<button className="workspace-toggle" onClick={()=>{if(dirtyFiles.current){setError('Save or discard your file edits first.');return;}setDetails(true);setTab('preview');if(window.matchMedia('(max-width: 620px)').matches)setFocusPreview(true)}}><Code2 size={15}/>Workspace</button>}<button className="icon-button" aria-label="Open environments" onClick={()=>setEnvironments(true)}><Settings2 size={17}/></button><button className="logout-button" disabled={loading} onClick={async()=>{try{await getJSON('/api/logout',{method:'POST'});fresh();setHistory([]);setTasks([]);setSession({...session,authenticated:false})}catch(cause){setError(cause.message)}}}>Sign out</button></div></header>
  {error&&<div className="error-banner" role="alert"><span>{error}</span><button onClick={()=>setError('')} aria-label="Dismiss error"><X size={15}/></button></div>}
  <div className={`workbench ${details&&selected?'with-preview':''}`}><section className="chat-column" aria-label="Conversation"><header className="chat-panel-heading"><span><Sparkles size={15}/> Kaelia</span><button className="icon-button" aria-label="New conversation" disabled={loading} onClick={fresh}><Plus size={15}/></button></header><div className={`chat-scroll ${messages.length?'has-messages':''}`}>
  {opening?<div className="conversation-loading" role="status"><LoaderCircle className="spin" size={18}/>Opening conversation…</div>:messages.length===0?<div className="chat-welcome"><div className="hero-orb"><Sparkles size={29}/></div><div className="eyebrow">YOUR NEXT IDEA STARTS HERE</div><h1>Think it.<br/><em>Make it real.</em></h1><p>Ask a question. Explore your project. Build something new.<br/>One space for your next big thing.</p><div className="suggestion-grid">{['Explain the current project','Review the code without changing it','Build a simple interactive page'].map((text,index)=><button key={text} onClick={()=>setPrompt(text)}><span>0{index+1}</span>{text}<ArrowUp size={14}/></button>)}</div></div>:<div className="chat-messages">{messages.map(message=><article key={message.id} className={`unified-message ${message.role} ${message.kind==='error'?'message-error':''}`}><div className="message-avatar">{message.role==='assistant'?<Code2 size={16}/>:'You'}</div><div className="message-content"><span className="message-author">{message.role==='assistant'?'Kaelia':'You'}</span><MessageBody content={message.content}/>{message.generated_images?.length>0&&<GeneratedImages images={message.generated_images} disabled={loading||opening} onEdit={editGeneratedImage}/>} {message.image_count>0&&<small className="message-cost">{message.image_count} reference image{message.image_count===1?'':'s'}</small>}{message.inspection?.length>0&&<div className="inspection-note"><ShieldCheck size={12}/>{message.inspection.join(' · ')} · Read-only</div>}{message.task_id===selected&&<LiveProgress events={events} status={activeTask?.status}/>} {message.project_images?.length>0&&<div className="project-image-links"><button type="button" onClick={()=>showTask(message.task_id,'live')}><Activity size={14}/>View images in Live</button></div>} {message.task_id&&message.kind!=='build'&&!message.project_images?.length&&<WorkCard task={tasks.find(task=>task.id===message.task_id)} taskId={message.task_id} onOpen={showTask} onResume={message.task_id===messages.findLast(item=>item.task_id)?.task_id?resumeWork:undefined} routingCost={message.cost_record} onPause={pauseWork}/>} {message.local_response&&!message.task_id&&message.role==='assistant'&&<small className="message-cost">No model call · $0</small>}{message.cost_record&&(!message.task_id||message.kind==='build'||message.project_images?.length)&&<UsageLine records={[message.cost_record]}/>}</div></article>)}{loading&&<div className="thinking-state" role="status" aria-label="Kaelia is thinking"><div className="message-avatar"><Code2 size={16}/></div><span>Thinking</span>{pendingUsage&&<UsageLine records={[pendingUsage]}/>}<div className="thinking-dots" aria-hidden="true"><i/><i/><i/></div></div>}</div>}<div ref={bottom}/></div>
  <div className="chat-composer-area">{health?.can_prepare_tasks&&<div className="setup-strip"><span><Sparkles size={14}/>Enable the execution environment when you're ready to build.</span><button disabled={preparing} onClick={prepare}>{preparing?<><LoaderCircle className="spin" size={12}/>Preparing…</>:'Prepare environment'}</button><small>Uses your sandbox allowance. Does not call an AI model.</small></div>}<form className="unified-composer" onSubmit={submit}>{images.length>0&&<div className="reference-images">{images.map((image,index)=><div key={index}><img src={image.url} alt={image.name}/><button type="button" aria-label={`Remove ${image.name}`} onClick={()=>setImages(previous=>previous.filter((_,i)=>i!==index))}><X size={12}/></button></div>)}</div>}<textarea onPaste={pasteImages} aria-label="Message Kaelia" placeholder="Ask anything, or describe what you want to build…" maxLength={10000} value={prompt} onChange={event=>setPrompt(event.target.value)} rows={3} onKeyDown={event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.nativeEvent.isComposing){event.preventDefault();event.currentTarget.form.requestSubmit()}}}/><div className="unified-composer-bottom"><input ref={imageInput} type="file" accept="image/png,image/jpeg" multiple hidden onChange={attachImages}/><button type="button" className="icon-button" aria-label="Attach reference images" title="Attach PNG or JPEG, up to 1 MB each" disabled={loading||images.length>=2} onClick={()=>imageInput.current.click()}><ImagePlus size={16}/></button><button type="button" className="icon-button" aria-label="Open Image Studio" title="Image Studio" onClick={openStudio}><Image size={16}/></button><button type="button" className="model-launch" onClick={()=>setAccount('models')}><Bot size={14}/>{models.find(model=>model.id===modelId)?.label||'Choose model'}</button><button type="button" className="composer-wallet" aria-label="Open PAYG wallet" onClick={()=>setAccount('wallet')}><Wallet size={14}/></button><button className="unified-send" type="submit" disabled={loading||opening||(!prompt.trim()&&!images.length)} aria-label="Send message">{loading?<LoaderCircle className="spin" size={18}/>:<ArrowUp size={18}/>}</button></div></form><p className="composer-footnote">One conversation. Questions stay questions. Changes come with real progress.<span>Usage estimates in USD.</span></p></div></section>
  {(details||!selected)&&<aside className="workbench-panel" data-view={tab} aria-label="Project workspace"><header className="workspace-panel-header"><div><button className={tab==='preview'?'active':''} onClick={()=>changeWorkspaceTab('preview')}><Eye size={14}/>Preview</button><button className={tab==='studio'?'active':''} onClick={openStudio}><Image size={14}/>Studio</button><button className={tab==='browser'?'active':''} onClick={()=>changeWorkspaceTab('browser')}>Browser</button><button disabled={!selected} className={tab==='code'?'active':''} onClick={()=>changeWorkspaceTab('code')}><FileCode2 size={14}/>Code & files</button><button disabled={!selected} className={tab==='live'?'active':''} onClick={()=>changeWorkspaceTab('live')}><Activity size={14}/>Live</button><button disabled={!selected} className={tab==='activity'?'active':''} onClick={()=>changeWorkspaceTab('activity')}>Activity</button></div><button className="icon-button" aria-label={focusPreview?'Show chat':'Expand preview'} onClick={()=>setFocusPreview(!focusPreview)}>{focusPreview?<Minimize2 size={15}/>:<Maximize2 size={15}/>}</button></header><div className="workspace-panel-scroll">{tab==='studio'?<ImageStudio key={conversationId||'studio'} taskId={selected} runtime={activeTask?.runtime} conversationId={conversationId} locked={loading||opening||activeTask?.status==='running'||activeTask?.status==='queued'} draft={studioDrafts[studioKey]} onDraftChange={draft=>setStudioDrafts(previous=>Object.fromEntries([...Object.entries(previous).filter(([key])=>key!==studioKey),[studioKey,draft]].slice(-4)))} onOpenLive={value=>showTask(value,'live')} onGenerate={sendMessage} generatedImages={recentImages} onEditGeneratedImage={editGeneratedImage} onSave={value=>adoptStudioRevision(value,selected,conversationId)} onOpenFile={(value,path)=>{setLiveFile({taskId:value,path});showTask(value,'code')}} onAttach={attachStudioImage}/>:tab==='browser'?(activeTask?.model_id?.startsWith('codex')?<CodexBrowser key={selected} taskId={selected} running={activeTask?.status==='running'}/>:<BrowserPanel/>):!selected?<div className="canvas-placeholder"><div><Code2 size={32}/></div><h2>Your next app starts here</h2><p>Describe what you want to build in the chat.<br/>Your application will appear in this workspace.</p><span>Preview · Source files · Build logs</span></div>:tab==='preview'?<ProjectPreview key={`${selected}:${previewNonce}`} taskId={selected} initialOpen onRuntime={setPreviewRuntime} revision={`${events.filter(event=>event.event==='preview_updated').length}:${activeTask?.status}:${activeTask?.build_pending||false}`}/>:tab==='live'?<LivePanel key={selected} task={activeTask} events={events} logsReady={eventState.taskId===selected} logsComplete={eventState.complete} onOpenFile={openLiveFile}/>:tab==='code'?<ProjectFiles key={`${selected}:${liveFile?.taskId===selected?liveFile.path:''}`} initialPath={liveFile?.taskId===selected?liveFile.path:''} taskId={selected} revision={activeTask?.updated_at} conversationId={conversationId} onRevision={adoptRevision} onDirtyChange={value=>{dirtyFiles.current=value}}/>:<TaskView {...{activeTask,events,logRef,threadRuns,cancelSelected}} selectTask={setSelected} logsReady={eventState.taskId===selected} logsComplete={eventState.complete} latestAssistant={events.findLast(event=>event.event==='task_finished')} hidePreview/>}</div><Diagnostics key={selected||'empty'} taskId={selected} events={events} task={activeTask} previewMessages={previewRuntime.taskId===selected?previewRuntime.items:[]}/></aside>}</div></main></div>
}
