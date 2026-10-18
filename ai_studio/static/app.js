'use strict';
const $=id=>document.getElementById(id);
let token='', activeId=null, timer=null, statusCache=null, currentWorkflow='research';
const KEY_NAMES={OpenAI:'OPENAI_API_KEY',Claude:'ANTHROPIC_API_KEY',Gemini:'GEMINI_API_KEY',Groq:'GROQ_API_KEY'};
function tell(message,error=false){const el=$('feedback');el.textContent=message;el.classList.toggle('error',error);}
async function api(path,opts={}){
 if(!token)throw Error('Connect your private workspace first.');
 const response=await fetch(path,{...opts,headers:{'X-Studio-Token':token,'Content-Type':'application/json',...(opts.headers||{})},cache:'no-store'});
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
 }catch(e){
  token='';$('access-state').textContent='● Not connected';$('access-state').className='status-pill neutral';
  $('health-auth').textContent='Not connected';
  tell(e.message,true);
 }finally{$('connect').disabled=false;}
}
function providerCard(name,config){
 const el=document.createElement('div');el.className='provider';
 const label=document.createElement('label');
 const input=document.createElement('input');input.type='checkbox';input.value=name;input.checked=Boolean(config.configured);label.append(input,document.createTextNode(name));
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
 $('count').textContent=count+'/4';$('health-keys').textContent=count+' of 4 keys saved';
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
function block(title,body){
 const el=document.createElement('div');el.className='block';
 const h=document.createElement('h3');h.textContent=title;
 const p=document.createElement('pre');p.textContent=String(body??'');
 el.append(h,p);$('output').appendChild(el);
}
async function history(){
 const data=await api('/api/jobs');$('history').replaceChildren();
 if(!data.length){const empty=document.createElement('p');empty.className='placeholder';empty.textContent='No missions yet. Start with a free practice run above.';$('history').appendChild(empty);}
 for(const job of data){
  const button=document.createElement('button');button.type='button';button.className='job';
  const meta=document.createElement('small');meta.textContent=job.workflow+' · '+job.status+' · '+job.created.slice(0,16).replace('T',' ');
  button.append(document.createTextNode(job.topic.slice(0,135)),meta);
  button.onclick=()=>showJob(job.id).catch(e=>tell(e.message,true));$('history').appendChild(button);
 }
}
async function showJob(id){
 activeId=id;clearTimeout(timer);
 const result=await api('/api/jobs/'+id);$('output').replaceChildren();
 const demo=Boolean(result.output?.simulation);
 block((demo?'FREE PRACTICE':'MISSION')+' · '+result.status.toUpperCase(),result.topic);
 if(result.output){
  if(result.output.notice)block('What this means',result.output.notice);
  if(result.output.error)block('Issue',result.output.error);
  for(const entry of result.output.initial||[])block('Perspective · '+entry.provider,entry.answer||entry.error||'No response');
  for(const entry of result.output.cross_review||[])block('Peer feedback · '+entry.provider,entry.answer||entry.error||'No response');
  if(result.output.synthesis)block('Combined answer',result.output.synthesis);
  if(result.output.completed!==undefined)block('Mission summary',
   result.output.completed+'/'+result.output.requested+' first-round participants. Brokerage orders: disabled.'+
   (demo?' All answers above are simulations, not genuine AI output.':''));
 }
 jump('history-panel');
 if(result.status==='running')timer=setTimeout(()=>showJob(id).catch(e=>tell(e.message,true)),3500);
}
async function launch(demo=false){
 const button=demo?$('practice'):$('launch');button.disabled=true;
 const selected=[...document.querySelectorAll('.provider input:checked')].map(el=>el.value);
 const providers=selected.length?selected:(demo?['OpenAI','Gemini','Groq']:[]);
 let topic=$('topic').value.trim();
 if(demo&&!topic){topic='Create a short team plan to research a new idea, check assumptions, and explain risks.';$('topic').value=topic;}
 try{
  if(topic.length<3)throw Error('Tell your team what you would like to explore.');
  if(!providers.length)throw Error('Select at least one AI teammate.');
  const request={topic,workflow:currentWorkflow,mode:$('mode').value,role:$('role').value,providers,demo};
  const job=await api('/api/jobs',{method:'POST',body:JSON.stringify(request)});
  tell(demo?'Practice mission started. All results are simulated; no AI calls were made.':'Live AI mission started. Check progress below.');
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
$('nav-history').onclick=()=>{navActive('nav-history');jump('history-panel');};
document.querySelectorAll('[data-jump]').forEach(b=>b.onclick=()=>{
 document.querySelectorAll('[data-jump]').forEach(x=>x.classList.toggle('mob-active',x===b));
 jump(b.dataset.jump);
});
