'use strict';
const $=id=>document.getElementById(id);
let token='', activeId=null, timer=null, statusCache=null, currentWorkflow='research';
const importedFiles=[];
const MAX_IMPORT_BYTES=5*1024*1024, MAX_TOTAL_IMPORT_CHARS=18000;
const KEY_NAMES={OpenAI:'OPENAI_API_KEY',Claude:'ANTHROPIC_API_KEY',Gemini:'GEMINI_API_KEY',Groq:'GROQ_API_KEY','OpenRouter Free':'OPENROUTER_API_KEY'};
function tell(message,error=false){const el=$('feedback');el.textContent=message;el.classList.toggle('error',error);}
async function api(path,opts={}){
 if(!token)throw Error('Connect your private workspace first.');
 const response=await fetch(path,{...opts,headers:{'X-Studio-Token':token,...(opts.body instanceof FormData?{}:{'Content-Type':'application/json'}),...(opts.headers||{})},cache:'no-store'});
 let data;try{data=await response.json()}catch{throw Error('The server sent an unreadable response. Please try again.')}
 if(!response.ok)throw Error(data.error||'Request failed ('+response.status+').');
 return data;
}
function jump(id){$(id)?.scrollIntoView({behavior:'smooth',block:'start'});}
function navActive(id){
 for(const el of document.querySelectorAll('.main-nav .nav'))el.classList.toggle('active',el.id===id);
}
function chooseWorkflow(workflow){
 currentWorkflow=workflow;
 document.querySelectorAll('.tile').forEach(tile=>tile.classList.toggle('selected',tile.dataset.workflow===workflow));
 const names={research:'Research',investment:'Investing research · no orders',project:'Project planning'};
 $('workflow-tag').textContent=names[workflow]||'Research';
 navActive(workflow==='investment'?'nav-investment':workflow==='project'?'nav-project':'nav-research');
 jump('mission');
}
async function connect(){
 const entered=$('token').value.trim();
 if(!entered){tell('Enter your private Studio access code first.',true);return;}
 token=entered;$('token').value='';$('connect').disabled=true;
 try{
  await loadProviders(); await history();await loadHealth();
  $('access-state').textContent='● Connected';$('access-state').className='status-pill positive';
  $('access-caption').textContent='Secure workspace connected. Your access code stays in this page only.';
  $('health-auth').textContent='Connected';
  tell('Connected! Your team and tools are ready for practice missions.');
  loadFinance().catch(()=>{});
  loadSourceStatus().catch(()=>{});
 }catch(e){
  token='';$('access-state').textContent='● Not connected';$('access-state').className='status-pill neutral';
  $('health-auth').textContent='Not connected';
  tell(e.message,true);
 }finally{$('connect').disabled=false;}
}
function providerCard(name,config){
 const el=document.createElement('div');el.className='provider';
 const label=document.createElement('label');
 const input=document.createElement('input');input.type='checkbox';input.value=name;input.checked=Boolean(config.configured)&&name!=='OpenAI';label.append(input,document.createTextNode(name));
 const key=document.createElement('small');key.className=config.configured?'saved':'missing';
 key.textContent=config.configured?'✓ API key saved':'○ Key missing (practice still works)';
 const model=document.createElement('small');model.textContent=config.model||'Model not specified';
 el.append(label,key,model);return el;
}
async function loadProviders(){
 const data=await api('/api/providers');statusCache=data;
 $('providers').replaceChildren();
 const entries=Object.entries(data.providers),count=entries.filter(([name,cfg])=>cfg.configured).length;
 for(const [name,config] of entries)$('providers').appendChild(providerCard(name,config));
 $('count').textContent=count+'/'+entries.length;$('health-keys').textContent=count+' of '+entries.length+' keys saved';
 $('missing-keys').replaceChildren();
 const missing=entries.filter(([name,cfg])=>!cfg.configured);
 for(const [name] of missing){const el=document.createElement('code');el.textContent=KEY_NAMES[name];$('missing-keys').appendChild(el);}
 $('provider-guidance').textContent=missing.length?'Missing in Studio: '+missing.map(([name])=>name).join(', ')+'. Practice mode works without these.':'All provider key names are present. Live authorization still requires a separate test.';
 const usage=data.usage||{api_usage_enabled:data.api_usage_enabled,daily_limit:0,remaining:0};
 const liveAllowed=usage.api_usage_enabled&&usage.remaining>0;
 $('launch').disabled=!liveAllowed;
 $('launch').textContent=liveAllowed?'Run with real AI · '+usage.remaining+' requests left':'Run with real AI · locked';
 $('usage').textContent=liveAllowed
  ?'Live AI requests enabled. Provider billing and quotas can still apply; the service reserves calls against its daily limit before starting.'
  :'SAFE MODE: Real AI calls are off or the request cap is zero. Practice missions use simulated results at no API cost.';
}
async function loadHealth(){
 const data=await api('/api/self-test');
 $('practice-state').textContent=data.practice_mode==='available'?'Ready':'Unavailable';
 $('health-live').textContent=data.live_model_calls_tested?'Tested':'Not tested';
 $('health-note').textContent='Storage: '+data.storage+'. Provider authentication: '+data.provider_authentication+
  '. Practice exercises the job pipeline with clearly simulated content. History can be lost on free hosting.';
 return data;
}
function chatMessage(parent,{role='assistant',name='Nexus',text='',subtitle='',status=''}) {
 const turn=document.createElement('article');turn.className='chat-turn '+(role==='user'?'chat-user':'chat-assistant');
 if(status)turn.classList.add('chat-'+status);
 const avatar=document.createElement('span');avatar.className='chat-avatar';
 avatar.setAttribute('aria-hidden','true');avatar.textContent=role==='user'?'Y':name==='Nexus'?'✦':name.slice(0,1).toUpperCase();
 const content=document.createElement('div');content.className='chat-message';
 const header=document.createElement('div');header.className='chat-message-head';
 const who=document.createElement('strong');who.textContent=name;header.appendChild(who);
 if(subtitle){const sub=document.createElement('span');sub.textContent=subtitle;header.appendChild(sub);}
 content.appendChild(header);
 const body=window.NexusMarkdown?window.NexusMarkdown.render(text):
  document.createTextNode(String(text));
 const bodyHolder=document.createElement('div');bodyHolder.className='chat-message-body';
 bodyHolder.appendChild(body);content.appendChild(bodyHolder);
 if(role!=='user'&&text){
  const actions=document.createElement('div');actions.className='chat-message-actions';
  const copy=document.createElement('button');copy.type='button';copy.className='answer-copy';
  copy.textContent='Copy answer';copy.setAttribute('aria-label','Copy '+name+' answer');
  copy.addEventListener('click',()=>window.NexusMarkdown?.copyText(text,copy));
  actions.appendChild(copy);content.appendChild(actions);
 }
 turn.append(avatar,content);parent.appendChild(turn);
 return turn;
}
function chatNotice(parent,message,warning=false){
 const note=document.createElement('div');note.className='chat-notice'+(warning?' warning':'');
 note.textContent=message;parent.appendChild(note);
}
function chatTeamDetails(parent,output) {
 const first=output.initial||[],reviews=output.cross_review||[];
 if(!first.length&&!reviews.length)return;
 const details=document.createElement('details');details.className='chat-team-details';
 const summary=document.createElement('summary');
 summary.textContent='View AI team analysis · '+first.length+' initial response(s) · '+reviews.length+' peer review(s)';
 details.appendChild(summary);
 const inner=document.createElement('div');inner.className='chat-team-content';
 for(const entry of first){
  chatMessage(inner,{name:entry.provider||'Agent',text:entry.answer||entry.error||'No response',
   subtitle:'Independent finding',status:entry.status==='failed'?'failed':''});
 }
 for(const entry of reviews){
  chatMessage(inner,{name:entry.provider||'Agent',text:entry.answer||entry.error||'No response',
   subtitle:'Cross-review',status:entry.status==='failed'?'failed':''});
 }
 details.appendChild(inner);parent.appendChild(details);
}
async function history(){
 const data=await api('/api/jobs');$('history').replaceChildren();
 if(!data.length){
  const empty=document.createElement('p');empty.className='placeholder';
  empty.textContent='No conversations yet. Launch a research mission above.';
  $('history').appendChild(empty);
 }
 for(const job of data){
  const button=document.createElement('button');button.type='button';button.className='job';
  button.dataset.jobId=job.id;button.setAttribute('aria-pressed',String(job.id===activeId));
  const meta=document.createElement('small');meta.textContent=job.workflow+' · '+job.status+' · '+job.created.slice(0,16).replace('T',' ');
  button.append(document.createTextNode(job.topic.slice(0,135)),meta);
  button.onclick=()=>showJob(job.id).catch(e=>tell(e.message,true));$('history').appendChild(button);
 }
}
async function showJob(id,{focus=true}={}){
 activeId=id;clearTimeout(timer);
 const result=await api('/api/jobs/'+id);
 if(activeId!==id)return;
 const display=$('output');display.replaceChildren();
 const out=result.output||{},demo=Boolean(out.simulation),status=result.status;
 const thread=document.createElement('div');thread.className='chat-thread';
 const heading=document.createElement('div');heading.className='chat-thread-head';
 const title=document.createElement('strong');title.textContent='Conversation';
 const meta=document.createElement('span');meta.textContent=(result.workflow||'research')+' · '+
  (demo?'Practice simulation':status==='running'?'Working':status==='complete'?'Complete':status);
 heading.append(title,meta);thread.appendChild(heading);
 chatMessage(thread,{role:'user',name:'You',text:result.topic});
 if(status==='running'){
  const typing=document.createElement('div');typing.className='chat-thinking';
  const dot=document.createElement('span');dot.className='chat-thinking-icon';dot.textContent='✦';
  const copy=document.createElement('div');
  const label=document.createElement('strong');label.textContent='Nexus is working on your answer';
  const explanation=document.createElement('p');
  explanation.textContent='Your agents may be comparing findings. The completed response will appear here.';
  const pulse=document.createElement('span');pulse.className='chat-thinking-dots';
  pulse.textContent='● ● ●';pulse.setAttribute('aria-hidden','true');
  copy.append(label,explanation,pulse);typing.append(dot,copy);thread.appendChild(typing);
 }else {
  if(demo)chatNotice(thread,'PRACTICE ONLY — these are scripted examples, not real AI research.',true);
  if(out.notice)chatNotice(thread,out.notice,demo);
  if(out.error)chatNotice(thread,'Mission error: '+out.error,true);
  if(out.mode==='team'){
   if(out.synthesis){
    chatMessage(thread,{name:'Nexus',subtitle:'Combined team answer',
     text:out.synthesis,status:status==='failed'?'failed':''});
   }else if((out.initial||[]).length){
    chatNotice(thread,'A combined answer was not produced. Review the individual agent findings below.',true);
   }
   chatTeamDetails(thread,out);
  }else{
   for(const entry of out.initial||[]){
    chatMessage(thread,{name:entry.provider||'AI Agent',subtitle:'Independent answer',
     text:entry.answer||entry.error||'No response',status:entry.status==='failed'?'failed':''});
   }
   if((out.cross_review||[]).length)chatTeamDetails(thread,{cross_review:out.cross_review});
  }
  if(out.completed!==undefined){
   const foot=document.createElement('div');foot.className='chat-run-summary';
   foot.textContent=out.completed+' of '+out.requested+' agents responded · Team: '+
    (out.team_status||'practice')+' · Brokerage orders: disabled';
   thread.appendChild(foot);
  }else if(status==='interrupted'||status==='failed'){
   chatNotice(thread,'This mission did not complete. Review the error above or start another mission.',true);
  }
 }
 display.appendChild(thread);
 for(const button of $('history').querySelectorAll('button.job')){
  button.setAttribute('aria-pressed',String(button.dataset.jobId===id));
 }
 if(focus)jump('history-panel');
 if(status==='running')timer=setTimeout(()=>showJob(id,{focus:false}).catch(e=>tell(e.message,true)),3500);
}
function drawImportedFiles(){
 const list=$('file-list');list.replaceChildren();
 for(let i=0;i<importedFiles.length;i++){
  const item=importedFiles[i],line=document.createElement('div');line.className='file-item';
  const summary=document.createElement('span');
  summary.textContent=item.name+' · '+item.text.length.toLocaleString()+' characters'+(item.truncated?' · shortened':'');
  const remove=document.createElement('button');remove.type='button';remove.className='file-remove';
  remove.textContent='Remove';remove.setAttribute('aria-label','Remove '+item.name);
  remove.onclick=()=>{importedFiles.splice(i,1);drawImportedFiles();};
  line.append(summary,remove);list.appendChild(line);
 }
 $('file-status').textContent=importedFiles.length?
  importedFiles.length+' file(s) ready for your next mission ('+
  importedFiles.reduce((n,f)=>n+f.text.length,0).toLocaleString()+' characters extracted).':
  'No documents imported.';
}
async function importPhoneFiles(){
 const input=$('file-input'),selected=[...input.files];
 if(!selected.length)return;
 input.disabled=true;
 const problems=[];
 try{
  if(!token)throw Error('Connect your private workspace before importing a file.');
  for(const file of selected){
   if(importedFiles.length>=3){problems.push('You can attach at most three files.');break;}
   if(file.size>MAX_IMPORT_BYTES){problems.push(file.name+': larger than 5 MB.');continue;}
   if(importedFiles.some(f=>f.name===file.name)){problems.push(file.name+': already imported.');continue;}
   try{
    const body=new FormData();body.append('file',file,file.name);
    $('file-status').textContent='Importing '+file.name+'…';
    const parsed=await api('/api/files/extract',{method:'POST',body});
    const existing=importedFiles.reduce((sum,f)=>sum+f.text.length,0);
    if(existing+parsed.text.length>MAX_TOTAL_IMPORT_CHARS){
     problems.push(file.name+': combined text exceeds 18,000 characters. Remove a file first.');
     continue;
    }
    importedFiles.push(parsed);
   }catch(e){problems.push(file.name+': '+e.message);}
  }
 }catch(e){problems.push(e.message);}
 finally{
  input.value='';input.disabled=false;drawImportedFiles();
  if(problems.length)tell(problems.join(' '),true);
  else tell('Document import complete. Tell your AI team what to analyze.');
 }
}
$('file-input').addEventListener('change',importPhoneFiles);

async function launch(demo=false){
 const button=demo?$('practice'):$('launch');button.disabled=true;
 const selected=[...document.querySelectorAll('.provider input:checked')].map(el=>el.value);
 const providers=selected.length?selected:(demo?['OpenAI','Gemini','Groq']:[]);
 let topic=$('topic').value.trim();
 if(!topic&&importedFiles.length){topic='Analyze my attached files, summarize key findings, identify inconsistencies, and suggest next steps.';$('topic').value=topic;}
 if(demo&&!topic){topic='Create a short team plan to research a new idea, check assumptions, and explain risks.';$('topic').value=topic;}
 try{
  if(topic.length<3)throw Error('Tell your team what you would like to explore.');
  if(!providers.length)throw Error('Select at least one AI teammate.');
  const request={topic,workflow:currentWorkflow,mode:$('mode').value,role:$('role').value,providers,demo,
   attachments:importedFiles.map(({name,text})=>({name,text}))};
  const job=await api('/api/jobs',{method:'POST',body:JSON.stringify(request)});
  tell(demo?'Practice mission started. Documents were imported, but practice answers are simulated and do not analyze their contents.':'Live AI mission started with '+importedFiles.length+' imported file(s). Check progress below.');
  await history();await showJob(job.id);
 }catch(e){tell(e.message,true);}finally{button.disabled=false;}
}
async function loadFinance(){
 const target=$('finance-status');target.textContent='Checking existing GitHub workflows…';
 try{
  const data=await api('/api/finance/status');target.replaceChildren();
  const pre=document.createElement('p');pre.className='muted-note';pre.textContent='Latest GitHub Actions status (not proof of research accuracy or trading readiness).';target.appendChild(pre);
  const prefix='https://github.com/jacarney1990/alpaca-paper-monitor/actions/runs/';
  for(const wf of data.workflows||[]){
   const card=document.createElement('div');card.className='block';
   const title=document.createElement('h3');title.textContent=wf.name;
   const info=document.createElement('p');info.textContent=(wf.conclusion==='success'?'✓ ':wf.conclusion==='failure'?'⚠ ':'○ ')+wf.conclusion+' · '+wf.updated.slice(0,16).replace('T',' ');
   card.append(title,info);
   if(wf.url&&wf.url.startsWith(prefix)&&/^\d+$/.test(wf.url.slice(prefix.length))){
    const a=document.createElement('a');a.href=wf.url;a.rel='noopener noreferrer';a.target='_blank';a.textContent='View details on GitHub ↗';card.appendChild(a);
   }target.appendChild(card);
  }
 }catch(e){target.textContent=e.message;}
}
$('connect').onclick=connect;
$('token').addEventListener('keydown',e=>{if(e.key==='Enter')connect();});
$('practice').onclick=()=>launch(true);
$('launch').onclick=()=>launch(false);
$('reload').onclick=()=>history().catch(e=>tell(e.message,true));
$('run-health').onclick=()=>loadHealth().then(()=>tell('System status checked.')).catch(e=>tell(e.message,true));
$('check-finance').onclick=loadFinance;
document.querySelectorAll('.tile').forEach(b=>b.onclick=()=>chooseWorkflow(b.dataset.workflow));
$('nav-command').onclick=()=>{navActive('nav-command');jump('home');};
$('nav-research').onclick=()=>chooseWorkflow('research');
$('nav-investment').onclick=()=>{chooseWorkflow('investment');if(token)loadFinance();};
$('nav-project').onclick=()=>chooseWorkflow('project');
$('nav-sources').onclick=()=>{navActive('nav-sources');jump('data-hub');};
$('nav-history').onclick=()=>{navActive('nav-history');jump('history-panel');};
document.querySelectorAll('[data-jump]').forEach(b=>b.onclick=()=>{
 document.querySelectorAll('[data-jump]').forEach(x=>x.classList.toggle('mob-active',x===b));
 jump(b.dataset.jump);
});

async function verifyKeys(){
 const button=$('verify-keys');button.disabled=true;
 $('verify-results').textContent='Checking provider authentication safely…';
 try{
  const data=await api('/api/providers/check',{method:'POST'});
  $('verify-results').replaceChildren();
  for(const [name,check] of Object.entries(data.providers)){
   const item=document.createElement('div');item.className='verify-row';
   const label=document.createElement('strong');label.textContent=name;
   const status=document.createElement('span');status.textContent=check.status+' · '+check.detail;
   status.className=check.status==='valid'?'verified':'not-verified';
   item.append(label,status);$('verify-results').appendChild(item);
  }
  tell('API key authentication checked. This does not test live AI answers or incur model-generation charges.');
 }catch(error){
  $('verify-results').textContent=error.message;
  tell(error.message,true);
 }finally{button.disabled=false;}
}
$('verify-keys').onclick=verifyKeys;


// Source Hub: official allowlisted links, text-only rendering, no paid AI or orders.
const SOURCE_HOSTS=/^https:\/\/(www\.sec\.gov|data\.sec\.gov|api\.fda\.gov|fred\.stlouisfed\.org)\//i;
function sourceLine(parent,label,value){
 const line=document.createElement('p');line.className='source-line';
 const strong=document.createElement('strong');strong.textContent=label+' ';
 line.append(strong,document.createTextNode(String(value??'')));parent.appendChild(line);
}
function sourceLink(parent,url,label){
 if(typeof url!=='string'||!SOURCE_HOSTS.test(url))return;
 const a=document.createElement('a');a.href=url;a.target='_blank';a.rel='noopener noreferrer';
 a.textContent=label||'View original record ↗';parent.appendChild(a);
}
async function loadSourceStatus(){
 const data=await api('/api/sources/status');
 const target=$('source-status');target.replaceChildren();
 for(const [name,entry] of Object.entries(data.sources||{})){
  const label=document.createElement('span');label.className='source-chip';
  label.textContent='✓ '+name+' · '+entry.mode;target.appendChild(label);
 }
}
async function searchOfficialSource(source,payload,button){
 const box=$('source-results'),oldText=button.textContent;button.disabled=true;
 box.replaceChildren();
 const heading=document.createElement('h3');heading.textContent='Looking up '+source+' official records…';
 box.appendChild(heading);
 try{
  const data=await api('/api/sources/query',{method:'POST',
   body:JSON.stringify({source,...payload})});
  box.replaceChildren();
  const title=document.createElement('h3');title.textContent=source+' · '+(
   data.status==='ok'?'Official results':data.status==='no_results'?'No matching records':'Source status');
  box.appendChild(title);
  const note=document.createElement('p');note.className='muted-note';
  note.textContent=data.note||'Official data, not model-generated research.';box.appendChild(note);
  sourceLine(box,'Checked:',data.fetched_at?.replace('T',' ').slice(0,16)||'Now');
  for(const item of data.items||[]){
   const card=document.createElement('article');card.className='source-record';
   const header=document.createElement('strong');header.textContent=item.title||'Official record';
   const date=document.createElement('small');date.textContent=item.date||'Date unavailable';
   const desc=document.createElement('p');desc.textContent=item.summary||'Open official source for details';
   card.append(header,date,desc);
   if(item.value!==undefined)sourceLine(card,'Observation:',item.value);
   sourceLink(card,item.url,'Open official source ↗');
   box.appendChild(card);
  }
  if(!(data.items||[]).length){const empty=document.createElement('p');
   empty.className='placeholder';empty.textContent='No matches returned. Try a different ticker or search term.';
   box.appendChild(empty);}
  sourceLink(box,data.reference,'Official dataset ↗');
  if((data.items||[]).length){
   const use=document.createElement('button');use.type='button';
   use.className='btn secondary source-use';
   use.textContent='Use these official findings in a mission →';
   use.onclick=()=>{
    const passages=(data.items||[]).map(item=>
      '- '+(item.title||'Official record')+
      ' | '+(item.date||'date not supplied')+
      ' | '+(item.summary||'')+' | '+(item.url||''));
    const topicText='Review these '+source+' public records critically. Distinguish official metadata, '+
      'unverified conclusions, and research gaps. Do not recommend trades.\n'+
      passages.join('\n')+'\nOriginal source: '+(data.reference||'');
    $('topic').value=topicText.slice(0,5000);
    chooseWorkflow('research');
    tell('Public findings copied into your mission. Free practice is simulated; real AI requests still require authorization.');
   };
   box.appendChild(use);
  }
  tell(source+' source check complete. No AI tokens or brokerage calls used.');
 }catch(e){
  box.replaceChildren();
  const header=document.createElement('h3');header.textContent=source+' is unavailable';
  const body=document.createElement('p');body.textContent=e.message;
  box.append(header,body);
  tell(e.message,true);
 }finally{button.disabled=false;button.textContent=oldText;jump('source-results');}
}
$('sec-form').addEventListener('submit',e=>{
 e.preventDefault();searchOfficialSource('SEC',{ticker:$('sec-ticker').value},
  $('sec-form').querySelector('button[type=submit]'));
});
$('fda-form').addEventListener('submit',e=>{
 e.preventDefault();searchOfficialSource('FDA',{
  dataset:$('fda-dataset').value,query:$('fda-query').value},
  $('fda-form').querySelector('button[type=submit]'));
});
$('fred-form').addEventListener('submit',e=>{
 e.preventDefault();searchOfficialSource('FRED',{series:$('fred-series').value},
  $('fred-form').querySelector('button[type=submit]'));
});
