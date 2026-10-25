'use strict';
/* Layout-only enhancement. Reuses the established authenticated Nexus APIs.
   No browser storage, secrets in URLs, or external scripts. */
(function () {
 const $=id=>document.getElementById(id);
 const mk=(tag,cls,label)=>{
  const e=document.createElement(tag);
  if(cls)e.className=cls;
  if(label!==undefined)e.textContent=label;
  return e;
 };
 try {
 const main=document.querySelector('main.content');
 window.addEventListener('error',event=>{const el=document.getElementById('nexus-init-error')||document.createElement('div');el.id='nexus-init-error';el.style.cssText='position:fixed;bottom:70px;left:10px;right:10px;z-index:9999;background:#742b34;color:white;padding:12px;border-radius:8px;font-size:12px';el.textContent='Nexus interface error: '+event.message+' ('+(event.filename||'').split('/').pop()+':'+event.lineno+')';document.body.appendChild(el);});
 const side=document.querySelector('.sidebar');
 if(!main||!side||!$('mission')||!$('history-panel'))return;
 main.classList.add('nexus-chat-main');
 document.body.classList.add('nexus-chat-app');
 const nav=side.querySelector('.main-nav');
 const brand=side.querySelector('.brand');
 brand.querySelector('small').textContent='Your AI workspace';
 const menu=mk('button','nexus-mobile-menu','☰');
 menu.id='nexus-menu';menu.type='button';menu.setAttribute('aria-label','Open navigation');
 const newTop=mk('header','nexus-chat-top');
 const topTitle=mk('div','nexus-top-identity');
 topTitle.append(mk('span','nexus-top-star','✦'),mk('strong','', 'Nexus AI'));
 const state=mk('span','nexus-top-status','Connect to start');state.id='nexus-top-status';
 const topRight=mk('div','nexus-top-right');
 const settingsBtn=mk('button','nexus-icon-button','⚙');settingsBtn.id='nexus-open-settings';
 settingsBtn.type='button';settingsBtn.setAttribute('aria-label','Open settings');
 topRight.append(state,settingsBtn);
 newTop.append(menu,topTitle,topRight);
 main.prepend(newTop);

 const newBtn=mk('button','nexus-new-chat','＋  New conversation');
 newBtn.id='nexus-new-chat';newBtn.type='button';
 nav.before(newBtn);
 const historyHeading=mk('div','nexus-side-caption','Recent conversations');
 const sidebarHistory=mk('div','nexus-sidebar-history');
 const refresh=mk('button','nexus-history-refresh','↻ Refresh history');
 refresh.type='button';refresh.addEventListener('click',()=>{
  if(!token)return;
  history().catch(error=>tell(error.message,true));
 });
 sidebarHistory.append(historyHeading,refresh,$('history'));
 nav.after(sidebarHistory);

 const body=mk('div','nexus-body');
 const chatPane=mk('section','nexus-chat-pane');
 chatPane.id='nexus-chat-pane';
 const welcome=mk('section','nexus-chat-empty');
 welcome.id='nexus-chat-empty';
 welcome.append(mk('div','nexus-greeting-symbol','✦'));
 welcome.append(mk('h1','', 'What can we explore together?'));
 welcome.append(mk('p','', 'Ask a question, share a file, or bring several AI perspectives together.'));
 const suggestions=mk('div','nexus-suggestions');
 const choices=[
  ['⌕','Research an idea','Compare the strongest arguments and credible evidence.','research'],
  ['▣','Analyze a document','Summarize this file and highlight anything important.','research'],
  ['↗','Explore investments','Analyze an investment idea, major risks, and alternative scenarios.','investment'],
  ['◇','Plan a project','Break this project into practical milestones and next steps.','project']
 ];
 for(const [icon,title,prompt,workflow]of choices){
  const b=mk('button','nexus-suggestion');
  b.type='button';
  b.append(mk('span','nexus-suggestion-icon',icon),mk('strong','',title));
  b.addEventListener('click',()=>{
   if(window.NexusShell)window.NexusShell.show('chat');
   $('topic').value=prompt;
   if(typeof chooseWorkflow==='function')chooseWorkflow(workflow);
   $('topic').focus();
   if(title==='Analyze a document')$('file-input').click();
  });
  suggestions.appendChild(b);
 }
 welcome.appendChild(suggestions);
 chatPane.appendChild(welcome);

 const login=mk('div','nexus-chat-login');login.id='nexus-chat-login';
 login.appendChild($('settings'));chatPane.prepend(login);
 chatPane.appendChild($('history-panel'));
 const composerHolder=mk('div','nexus-composer-holder');
 // Status messages must remain visible after the sign-in panel collapses.
 composerHolder.appendChild($('feedback'));
 composerHolder.appendChild($('mission'));
 chatPane.appendChild(composerHolder);
 body.appendChild(chatPane);

 const toolsPane=mk('section','nexus-tool-pane');toolsPane.id='nexus-tool-pane';
 toolsPane.hidden=true;
 const sources=mk('div','nexus-tool-view');sources.id='nexus-view-sources';
 sources.appendChild($('data-hub'));
 const investing=mk('div','nexus-tool-view');investing.id='nexus-view-investing';
 investing.appendChild($('finance-workflows'));
 const system=mk('div','nexus-tool-view');system.id='nexus-view-system';
 system.append($('diagnostics'),document.querySelector('.overview-cards'));
 const investAsk=mk('button','btn primary','Ask Nexus to review an investing idea');
 investAsk.type='button';investAsk.addEventListener('click',()=>{
  show('chat');chooseWorkflow('investment');
  question.value='Help me evaluate an investing idea. Compare evidence, risks and alternative scenarios.';
  question.focus();
 });
 investing.appendChild(investAsk);
 const healthAsk=mk('button','btn secondary','Discuss system health with Nexus');
 healthAsk.type='button';healthAsk.addEventListener('click',()=>{
  show('chat');chooseWorkflow('project');
  question.value='Explain how to assess an AI platform for reliability and production readiness.';
  question.focus();
 });
 system.appendChild(healthAsk);
 const settings=mk('div','nexus-tool-view');settings.id='nexus-view-settings';
 settings.append($('share-panel'),document.querySelector('.settings-tip'));
 const legacyFooter=main.querySelector('.footer');if(legacyFooter)settings.appendChild(legacyFooter);
 const authBack=mk('div','nexus-settings-auth');
 settings.prepend(authBack);
 const logout=mk('button','nexus-logout','Disconnect workspace');
 logout.type='button';logout.addEventListener('click',()=>{
  token='';activeId=null;clearTimeout(timer);
  $('access-state').textContent='● Not connected';
  $('access-caption').textContent='Enter your private access code to use your AI team.';
  $('history').replaceChildren();$('output').replaceChildren();
  for(const id of ['providers','verify-results','source-status','source-results','finance-status','missing-keys']){
   const item=$(id);if(item)item.replaceChildren();
  }
  statusCache=null;
  $('launch').disabled=true;importedFiles.splice(0);drawImportedFiles();
  $('topic').value='';
  login.appendChild($('settings'));
  show('chat');sync();
 });
 authBack.append(mk('p','','Your private access code remains in this page memory while connected.'),logout);
 toolsPane.append(sources,investing,system,settings);
 body.appendChild(toolsPane);
 main.appendChild(body);
 const welcomeLegacy=main.querySelector('.welcome');
 if(welcomeLegacy)welcomeLegacy.remove();
 const oldTopbar=main.querySelector('.topbar');if(oldTopbar)oldTopbar.remove();
 const lower=main.querySelector('.lower-grid');if(lower)lower.remove();
 const stale=main.querySelector('.mobile-bar');if(stale)stale.remove();
 const mobile=document.querySelector('body > .mobile-bar');
 if(mobile)mobile.remove();

 const mission=$('mission');
 const missionHeading=mission.querySelector('.heading-row');
 if(missionHeading)missionHeading.remove();
 const workflowTiles=mission.querySelector('.workflow-tiles');
 if(workflowTiles)workflowTiles.remove();
 const composer=mission.querySelector('.composer');
 composer.classList.add('nexus-composer');
 const composerHeader=composer.querySelector(':scope > .section-head');
 if(composerHeader){
  const workflowTag=$('workflow-tag');
  if(workflowTag){workflowTag.hidden=true;composer.appendChild(workflowTag);}
  composerHeader.remove();
 }
 const label=composer.querySelector('label[for="topic"]');
 if(label)label.classList.add('sr-only');
 const question=$('topic');
 question.placeholder='Message Nexus…';
 question.rows=2;
 const submitRow=mk('div','nexus-composer-actions');
 const attach=mk('button','nexus-small-action','＋ Add files');
 attach.type='button';attach.id='nexus-attach';
 attach.addEventListener('click',()=>$('file-input').click());
 const adv=mk('button','nexus-small-action','⚙ AI team');
 adv.type='button';adv.id='nexus-team-toggle';adv.setAttribute('aria-expanded','false');
 const models=mk('span','nexus-models-label','Team · Research');
 models.id='nexus-models-label';
 const spacer=mk('span','nexus-flex');
 const send=mk('button','nexus-send','↑ Send');send.type='button';send.id='nexus-send';
 submitRow.append(attach,adv,models,spacer,send);
 const help=mk('div','nexus-composer-caption','AI research may incur provider charges · No brokerage orders');
 const options=mk('div','nexus-advanced');options.id='nexus-advanced';options.hidden=true;
 const optionsTitle=mk('div','nexus-option-title','Customize your AI team');
 options.append(optionsTitle);
 const fileImport=composer.querySelector('.file-import');
 fileImport.classList.add('nexus-file-import');
 const mode=composer.querySelector('.form-pair');
 const separator=composer.querySelector('.separator');
 const compact=composer.querySelector('.section-head.compact');
 const providers=$('providers');
 const keyTest=composer.querySelector('.key-test');
 const verify=$('verify-results');
 const usage=$('usage');
 for(const item of [fileImport,mode,separator,compact,providers,keyTest,verify,usage]){
  if(item)options.appendChild(item);
 }
 const actions=composer.querySelector('.mission-actions');
 const practice=$('practice');
 const launch=$('launch');
 if(actions){
  actions.classList.add('nexus-run-controls');
  actions.querySelectorAll('button').forEach(b=>b.classList.add('nexus-run-button'));
 }
 const imported=$('file-list');
 const filesPreview=$('nexus-attached-preview')||mk('div','nexus-attached-preview');
 filesPreview.id='nexus-attached-preview';
 if(filesPreview.parentElement!==composer)composer.insertBefore(filesPreview,question);
 composer.append(submitRow,help,options);
 // Keep original launch/practice actions so the existing security and usage controls apply.
 if(actions)options.appendChild(actions);

 const viewNames={chat:'Conversation',sources:'Official sources',investing:'Investing research',system:'System health',settings:'Settings'};
 const viewMap={sources,investing,system,settings};
 let currentView='chat';
 function show(view){
  const name=viewNames[view] ? view:'chat';
  currentView=name;
  if(name==='settings')authBack.prepend($('settings'));
  else if($('settings').parentElement===authBack)login.appendChild($('settings'));
  chatPane.hidden=name!=='chat';
  toolsPane.hidden=name==='chat';
  for(const [key,element]of Object.entries(viewMap))element.hidden=name!==key;
  nav.querySelectorAll('.nav').forEach(b=>b.classList.toggle('active',
   (name==='chat'&&b.id==='nav-command')||(name==='sources'&&b.id==='nav-sources')||
   (name==='investing'&&b.id==='nav-investment')||(name==='system'&&b.id==='nav-project')||
   (name==='settings'&&b.id==='nav-history')));
  document.body.classList.remove('nexus-sidebar-open');
  sync();
  window.scrollTo(0,0);
 }
 function clearChat(){
  activeId=null;
  question.value='';
  if(Array.isArray(importedFiles)){importedFiles.splice(0);if(typeof drawImportedFiles==='function')drawImportedFiles();}
  $('output').replaceChildren();
  $('history-panel').hidden=true;
  welcome.hidden=false;
  show('chat');
  question.focus();
  sidebarHistory.querySelectorAll('.job').forEach(b=>b.setAttribute('aria-pressed','false'));
 }
 function onJobShown(){
  welcome.hidden=true;
  $('history-panel').hidden=false;
  if(currentView!=='chat')show('chat');
 }
 function onHistory(){ /* Existing history() updates the relocated sidebar list. */ }
 function sync(){
  const connected=Boolean(token);
  document.body.classList.toggle('nexus-connected',connected);
  state.textContent=connected?'● Connected':'Connect';
  state.style.cursor='pointer';
  send.disabled=!connected||$('launch').disabled;
  send.title=connected? 'Send to your AI team':'Connect your workspace first';
  login.hidden=connected||currentView!=='chat';
  models.textContent=$('mode').value==='team'?'AI team · Automatic routing':'Independent AI';
 }
 function present(tool){
  show(tool);
  if(tool==='sources'&&token&&typeof loadSourceStatus==='function')loadSourceStatus().catch(()=>{});
  if(tool==='investing'&&token&&typeof loadFinance==='function')loadFinance().catch(()=>{});
 }
 newBtn.onclick=clearChat;
 menu.onclick=()=>{document.body.classList.toggle('nexus-sidebar-open');menu.setAttribute('aria-expanded',String(document.body.classList.contains('nexus-sidebar-open')));};
 settingsBtn.onclick=()=>present('settings');
 state.onclick=()=>{present('settings');$('token')?.focus();};
 $('nav-command').onclick=()=>show('chat');
 $('nav-research').onclick=()=>{show('chat');chooseWorkflow('research');};
 $('nav-investment').onclick=()=>present('investing');
 $('nav-project').onclick=()=>{show('chat');chooseWorkflow('project');};
 $('nav-sources').onclick=()=>present('sources');
 const systemNav=mk('button','nav','◈ System health');
 systemNav.type='button';systemNav.id='nexus-nav-system';
 systemNav.onclick=()=>present('system');nav.appendChild(systemNav);
 $('nav-history').onclick=()=>{show('chat');document.body.classList.toggle('nexus-sidebar-open');};
 adv.onclick=()=>{
  options.hidden=!options.hidden;adv.setAttribute('aria-expanded',String(!options.hidden));
  adv.textContent=options.hidden?'⚙ AI team':'⌃ Close options';
 };
 send.onclick=()=>{if(!$('launch').disabled)launch(false);};
 question.addEventListener('keydown',e=>{
  if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){
   e.preventDefault();if(!$('launch').disabled)launch(false);
  }
 });
 $('mode').addEventListener('change',sync);
 document.querySelectorAll('.tile').forEach(tile=>tile.addEventListener('click',sync));
 $('connect').addEventListener('click',()=>setTimeout(sync,250));
 $('launch').addEventListener('click',()=>setTimeout(sync,250));
 $('practice').addEventListener('click',()=>setTimeout(sync,250));
 $('reload').addEventListener('click',()=>setTimeout(sync,250));
 const sheet=mk('div','nexus-drawer-backdrop');sheet.id='nexus-drawer-backdrop';document.body.appendChild(sheet);
 sheet.onclick=()=>document.body.classList.remove('nexus-sidebar-open');
 const bottom=mk('nav','nexus-mobile-tabs');bottom.setAttribute('aria-label','Nexus mobile navigation');
 for(const [label,view,emoji]of [['Chat','chat','✦'],['Sources','sources','⌕'],['Investing','investing','↗'],['Settings','settings','⚙']]){
  const b=mk('button','',emoji+' '+label);b.type='button';
  b.addEventListener('click',()=>present(view));bottom.appendChild(b);
 }
 document.body.appendChild(bottom);
 const oldHistoryTitle=$('history-panel').querySelector('.section-head');
 if(oldHistoryTitle)oldHistoryTitle.hidden=true;
 $('history-panel').hidden=true;
 for(const item of document.querySelectorAll('.nexus-tool-view'))item.hidden=true;
 // The user can still open every original tool, but the first screen is a clean chat.
 window.NexusShell={show,present,clearChat,onJobShown,onHistory,sync};
 sync();
 } catch (error) {
  const panel=document.createElement('div');panel.id='nexus-init-failure';panel.setAttribute('role','alert');panel.style.cssText='position:fixed;inset:auto 12px 75px;z-index:99999;padding:16px;background:#6b182b;color:white;border-radius:12px;overflow-wrap:anywhere';panel.textContent='Nexus initialization failed: '+String(error&&error.stack||error);document.body.appendChild(panel);
  console.error('Nexus chat shell failed',error);
 }
})();