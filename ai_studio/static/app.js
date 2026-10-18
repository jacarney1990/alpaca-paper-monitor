const $=id=>document.getElementById(id);
let token='', activeId=null, timer=null;
const tell=s=>{$('feedback').textContent=s};
async function api(path,opts={}){
 if(!token)throw Error('Enter your Studio access token first.');
 const r=await fetch(path,{...opts,headers:{'X-Studio-Token':token,'Content-Type':'application/json',...(opts.headers||{})},cache:'no-store'});
 let data;try{data=await r.json()}catch{throw Error('Server returned invalid JSON')}
 if(!r.ok)throw Error(data.error||('HTTP '+r.status));return data;
}
function block(title,body){
 const el=document.createElement('section');el.className='block';
 const h=document.createElement('h3');h.textContent=title;
 const p=document.createElement('pre');p.textContent=String(body??'');
 el.append(h,p);$('output').appendChild(el);
}
async function connect(){
 token=$('token').value.trim();$('token').value='';
 try{await loadProviders();await history();tell('Workspace connected; token not saved to browser storage.')}
 catch(e){token='';tell(e.message);}
}
async function loadProviders(){
 const data=await api('/api/providers');$('providers').replaceChildren();let n=0;
 for(const [name,v] of Object.entries(data.providers)){
  if(v.configured)n++;
  const el=document.createElement('div');el.className='provider';
  const lab=document.createElement('label'),cb=document.createElement('input');cb.type='checkbox';cb.value=name;
  cb.checked=!!v.configured;cb.disabled=!v.configured;
  lab.append(cb,document.createTextNode(name));
  const state=document.createElement('small');state.textContent=v.configured?'● KEY CONFIGURED':'○ NOT CONNECTED';
  if(!v.configured)state.className='notready';
  const model=document.createElement('small');model.textContent=v.model;
  el.append(lab,state,model);$('providers').appendChild(el);
 }
 $('count').textContent=n+'/4 configured';
 $('usage').textContent=data.api_usage_enabled?'AI API calls ENABLED on server; provider quotas may apply.':'AI API calls disabled (safe default).';
}
async function history(){
 const data=await api('/api/jobs');$('history').replaceChildren();
 if(!data.length){const p=document.createElement('p');p.textContent='No mission history yet.';$('history').appendChild(p);}
 for(const x of data){
  const b=document.createElement('button');b.className='job';
  const s=document.createElement('small');s.textContent=x.workflow+' · '+x.status+' · '+x.created.slice(0,16);
  b.append(document.createTextNode(x.topic.slice(0,120)),s);
  b.onclick=()=>showJob(x.id).catch(e=>tell(e.message));$('history').appendChild(b);
 }
}
async function showJob(id){
 activeId=id;clearTimeout(timer);
 const job=await api('/api/jobs/'+id);$('output').replaceChildren();
 block('MISSION · '+job.status.toUpperCase(),job.topic);
 if(job.output){
  if(job.output.error)block('Error',job.output.error);
  for(const x of job.output.initial||[])block('Independent · '+x.provider,x.answer||x.error);
  for(const x of job.output.cross_review||[])block('Peer review · '+x.provider,x.answer||x.error);
  if(job.output.synthesis)block('Team synthesis',job.output.synthesis);
  if(job.output.completed!==undefined)block('Participation',job.output.completed+' of '+job.output.requested+' providers completed first-round analysis; order execution disabled.');
 }
 if(job.status==='running')timer=setTimeout(()=>showJob(id).catch(e=>tell(e.message)),5000);
}
async function launch(){
 const btn=$('launch');btn.disabled=true;
 try{
  const providers=[...document.querySelectorAll('.provider input:checked')].map(el=>el.value);
  const data={topic:$('topic').value.trim(),workflow:$('workflow').value,mode:$('mode').value,role:$('role').value,providers};
  const job=await api('/api/jobs',{method:'POST',body:JSON.stringify(data)});
  tell('Mission accepted. Results shown here are actual provider replies, never fabricated examples.');
  await history();await showJob(job.id);
 }catch(e){tell(e.message)}finally{btn.disabled=false;}
}
$('connect').onclick=connect;$('launch').onclick=launch;
$('reload').onclick=()=>history().catch(e=>tell(e.message));
function chooseWorkflow(workflow){
 $('workflow').value=workflow;
 $('workflow').scrollIntoView({behavior:'smooth',block:'center'});
 tell('Selected '+$('workflow').selectedOptions[0].text+'. Enter your task and choose the AI providers.');
}
$('nav-command').onclick=()=>window.scrollTo({top:0,behavior:'smooth'});
$('nav-history').onclick=()=>$('history').scrollIntoView({behavior:'smooth',block:'start'});
$('nav-investment').onclick=()=>chooseWorkflow('investment');
$('nav-research').onclick=()=>chooseWorkflow('research');
$('nav-project').onclick=()=>chooseWorkflow('project');
