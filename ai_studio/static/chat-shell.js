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
 const main=document.querySelector('main.content');
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
 sidebarHistory.append(historyHeading,$('history'));
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
 const composer=mission.querySelector('.composer');
 composer.classList.add('nexus-composer');
 const composerHeader=composer.querySelector(':scope > .section-head');
 if(composerHeader)composerHeader.remove();
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
 options.append(optionsTitle,workflowTiles);
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
 const filesPreview=mk('div','nexus-attached-preview');
 filesPreview.id='nexus-attached-preview';
 composer.insertBefore(filesPreview,question);
 composer.append(submitRow,help,options);
 // Keep original launch/practice actions so the existing security and usage controls apply.
 if(actions)options.appendChild(actions);

 const viewNames={chat:'Conversation',sources:'Official sources',investing:'Investing research',system:'System health',settings:'Settings'};
 const viewMap={sources,investing,system,settings};
 let currentView='chat';
 function show(view){
  const name=viewNames[view] ? view:'chat';
  currentView=name;
  if(name==='settings')authBack.appendChild($('settings'));
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
  main.scrollIntoView({behavior:'smooth',block:'start'});
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
  state.textContent=connected?'● Workspace connected':'● Sign in required';
  send.disabled=!connected||$('launch').disabled;
  send.title=connected? 'Send to your AI team':'Connect your workspace first';
  login.hidden=connected||currentView!=='chat';
  models.textContent=($('workflow-tag')?.textContent||'Research')+' · '+($('mode').value==='team'?'AI team':'Independent');
 }
 function present(tool){
  show(tool);
  if(tool==='sources'&&token&&typeof loadSourceStatus==='function')loadSourceStatus().catch(()=>{});
  if(tool==='investing'&&token&&typeof loadFinance==='function')loadFinance().catch(()=>{});
 }
 newBtn.onclick=clearChat;
 menu.onclick=()=>document.body.classList.toggle('nexus-sidebar-open');
 settingsBtn.onclick=()=>present('settings');
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
 const originalConnect=$('connect').onclick;
 $('connect').onclick=async()=>{await originalConnect();sync();};
 const originalLaunch=$('launch').onclick;
 $('launch').onclick=async()=>{await originalLaunch();sync();};
 const originalPractice=$('practice').onclick;
 $('practice').onclick=async()=>{await originalPractice();sync();};
 const originalReload=$('reload').onclick;
 $('reload').onclick=async()=>{await originalReload();sync();};
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
})();