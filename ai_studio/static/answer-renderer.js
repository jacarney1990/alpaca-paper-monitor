'use strict';
/* Nexus answer renderer. No HTML interpolation or third-party Markdown.
   AI replies and uploaded-document text are always treated as untrusted data. */
(function () {
 const doc = document;
 const make = (tag,cls,text) => {
  const el=doc.createElement(tag);
  if(cls)el.className=cls;
  if(text!==undefined)el.textContent=String(text);
  return el;
 };
 function copyText(text,button) {
  if(!navigator.clipboard || !navigator.clipboard.writeText) {
   button.textContent='Copy unavailable';
   return;
  }
  navigator.clipboard.writeText(text).then(()=>{
   button.textContent='Copied';
   setTimeout(()=>{button.textContent='Copy';},1500);
  }).catch(()=>{button.textContent='Copy failed';});
 }
 function inline(parent,raw) {
  // All strings enter the DOM through textContent, never innerHTML.
  const re=/(\x60[^\x60\n]+\x60)|(\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\))|(\*\*([^*\n]+)\*\*)|(~~([^~\n]+)~~)|(\*([^*\n]+)\*)|(_([^_\n]+)_)|(https?:\/\/[^\s<>]+)/g;
  const content=String(raw ?? '');
  let cursor=0,match;
  while((match=re.exec(content))!==null) {
   if(match.index>cursor)parent.appendChild(doc.createTextNode(content.slice(cursor,match.index)));
   if(match[1])parent.appendChild(make('code','inline-code',match[1].slice(1,-1)));
   else if(match[2]) {
    const a=make('a','answer-link',match[3]);
    a.href=match[4];a.target='_blank';a.rel='noopener noreferrer';parent.appendChild(a);
   } else if(match[5])parent.appendChild(make('strong','',match[6]));
   else if(match[7])parent.appendChild(make('del','',match[8]));
   else if(match[9])parent.appendChild(make('em','',match[10]));
   else if(match[11])parent.appendChild(make('em','',match[12]));
   else if(match[13]) {
    const a=make('a','answer-link',match[13]);
    a.href=match[13];a.target='_blank';a.rel='noopener noreferrer';parent.appendChild(a);
   }
   cursor=re.lastIndex;
  }
  if(cursor<content.length)parent.appendChild(doc.createTextNode(content.slice(cursor)));
 }
 function paragraph(parent,lines) {
  const p=make('p');
  lines.forEach((line,i)=>{if(i)p.appendChild(doc.createElement('br'));inline(p,line);});
  parent.appendChild(p);
 }
 function codeBlock(parent,language,source) {
  const wrapper=make('div','answer-code');
  const toolbar=make('div','answer-code-toolbar');
  const label=make('span','',language||'Code');
  const copy=make('button','answer-copy','Copy');
  copy.type='button';copy.setAttribute('aria-label','Copy code');
  copy.addEventListener('click',()=>copyText(source,copy));
  toolbar.append(label,copy);
  const pre=make('pre');pre.appendChild(make('code','',source));
  wrapper.append(toolbar,pre);
  parent.appendChild(wrapper);
 }
 const isFence=line=>/^\s{0,3}(\x60\x60\x60|~~~)/.test(line);
 const isHeading=line=>/^\s{0,3}#{1,4}\s/.test(line);
 const isQuote=line=>/^\s{0,3}>\s?/.test(line);
 const isList=line=>/^\s{0,4}(?:[-*+]\s+|\d+[.)]\s+)/.test(line);
 const isRule=line=>/^\s{0,3}(?:(?:-\s*){3,}|(?:\*\s*){3,})$/.test(line);
 const tableDivider=line=>/^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(line);
 const tableCells=line=>line.trim().replace(/^\|/,'').replace(/\|$/,'').split('|').map(s=>s.trim());
 function render(markdown) {
  const root=make('div','answer-markdown');
  const lines=String(markdown??'').replace(/\r\n?/g,'\n').split('\n');
  let i=0;
  while(i<lines.length) {
   const line=lines[i];
   if(!line.trim()){i++;continue;}
   if(isFence(line)) {
    const match=line.trim().match(/^(\x60\x60\x60|~~~)\s*([\w+#-]*)/);
    const fence=match?match[1]:String.fromCharCode(96,96,96),language=match?match[2]:'';
    const body=[];i++;
    while(i<lines.length&&!lines[i].trim().startsWith(fence)){body.push(lines[i]);i++;}
    if(i<lines.length)i++;
    codeBlock(root,language,body.join('\n'));continue;
   }
   const title=line.match(/^\s{0,3}(#{1,4})\s+(.+)$/);
   if(title) {
    const heading=make('h'+Math.min(title[1].length+1,5));
    inline(heading,title[2].trim());root.appendChild(heading);i++;continue;
   }
   if(isRule(line)){root.appendChild(make('hr'));i++;continue;}
   if(i+1<lines.length&&line.includes('|')&&tableDivider(lines[i+1])) {
    const headers=tableCells(line),table=make('table'),head=make('thead'),headRow=make('tr');
    headers.forEach(value=>{const th=make('th');inline(th,value);headRow.appendChild(th);});
    head.appendChild(headRow);table.appendChild(head);
    const tbody=make('tbody');i+=2;
    while(i<lines.length&&lines[i].trim()&&lines[i].includes('|')) {
     const tr=make('tr'),values=tableCells(lines[i]);headers.forEach((_,j)=>{
      const td=make('td');inline(td,values[j]||'');tr.appendChild(td);
     });tbody.appendChild(tr);i++;
    }
    table.appendChild(tbody);const scroll=make('div','answer-table-scroll');scroll.appendChild(table);
    root.appendChild(scroll);continue;
   }
   if(isQuote(line)) {
    const quote=make('blockquote'),parts=[];
    while(i<lines.length&&isQuote(lines[i])){parts.push(lines[i].replace(/^\s{0,3}>\s?/,''));i++;}
    paragraph(quote,parts);root.appendChild(quote);continue;
   }
   if(isList(line)) {
    const ordered=/^\s{0,4}\d+[.)]\s+/.test(line);
    const list=make(ordered?'ol':'ul');i++;
    let currentLine=line;
    while(true) {
     const li=make('li');
     inline(li,currentLine.replace(/^\s{0,4}(?:[-*+]\s+|\d+[.)]\s+)/,''));
     list.appendChild(li);
     if(i>=lines.length||!isList(lines[i])||/^\s{0,4}\d+[.)]\s+/.test(lines[i])!==ordered)break;
     currentLine=lines[i];i++;
    }
    root.appendChild(list);continue;
   }
   const parts=[line];i++;
   while(i<lines.length&&lines[i].trim()&&!isFence(lines[i])&&!isHeading(lines[i])
    &&!isRule(lines[i])&&!isQuote(lines[i])&&!isList(lines[i])
    &&!(i+1<lines.length&&lines[i].includes('|')&&tableDivider(lines[i+1]))) {
    parts.push(lines[i]);i++;
   }
   paragraph(root,parts);
  }
  return root;
 }
 window.NexusMarkdown={render,copyText};
})();