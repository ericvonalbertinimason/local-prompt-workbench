const $=id=>document.getElementById(id);
let filter='',view='history',mode='iterate-prompt',offset=0,total=0,current=null,tab='result',parent=null,status=null;
const modeNames={'iterate-prompt':'Refine','optimize-user-prompt':'User prompt','optimize-system-prompt':'System prompt'};
async function api(path,body){const r=await fetch(path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Harness-Client':'workbench'},body:JSON.stringify(body)});const d=await r.json();if(!r.ok)throw Error(d.error||'Request failed');return d}
function toast(s){$('toast').textContent=s;$('toast').classList.add('show');setTimeout(()=>$('toast').classList.remove('show'),4000)}
function element(tag,text,cls){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e}
function selectMode(value){mode=value;document.querySelectorAll('[data-mode]').forEach(b=>b.classList.toggle('selected',b.dataset.mode===mode));$('mode-help').textContent={'iterate-prompt':'Calls iterate-prompt with your specific refinement instructions.','optimize-user-prompt':'Calls optimize-user-prompt for everyday tasks, writing, and questions.','optimize-system-prompt':'Calls optimize-system-prompt to develop agent roles, rules, and behavior.'}[mode]}
document.querySelectorAll('[data-mode]').forEach(b=>b.onclick=()=>selectMode(b.dataset.mode));
document.querySelectorAll('[data-view]').forEach(b=>b.onclick=()=>{view=b.dataset.view;offset=0;document.querySelectorAll('[data-view]').forEach(x=>x.classList.toggle('active',x===b));load().catch(e=>toast(e.message))});
document.querySelectorAll('[data-close]').forEach(b=>b.onclick=()=>$(b.dataset.close).close());
document.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>{tab=b.dataset.tab;renderDetail()});
$('new').onclick=()=>{parent=null;$('parent-label').textContent='';$('prompt').value='';$('requirements').value='';$('source').value='';updateSource();$('composer').scrollIntoView({behavior:'smooth'});$('prompt').focus()};
let searchTimer;$('search').oninput=()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>{offset=0;load().catch(e=>toast(e.message))},200)};
$('prev').onclick=()=>{offset=Math.max(0,offset-50);load().catch(e=>toast(e.message))};$('next').onclick=()=>{offset+=50;load().catch(e=>toast(e.message))};
async function load(){
 $('collection-title').textContent={history:'Prompt history',pinned:'Pinned prompts',library:'Saved library',archive:'Archived prompts',tests:'Test runs'}[view];
 $('collection-note').textContent=view==='library'?'Prompts you saved. Open one to copy or revise it; say "save it" after a prompt to add it.':view==='tests'?'Benchmark and quality-gate runs. Kept for comparison, out of your history.':view==='pinned'?'Pinned prompts also appear in Claude Code as slash commands once the workbench MCP server is added.':'Every run is kept here. Pin the useful ones; archive the rest.';
 let rows;
 if(view==='library'){const data=await api('/api/library');rows=data.rows.filter(x=>(x.title+' '+x.prompt).toLowerCase().includes($('search').value.toLowerCase()));total=rows.length;rows=rows.slice(offset,offset+50)}
 else{const data=await api('/api/runs?view='+view+'&offset='+offset+'&f='+filter+'&q='+encodeURIComponent($('search').value));rows=data.rows;total=data.total}
 $('list').replaceChildren();
 for(const row of rows){const card=element('button',undefined,'prompt-card');const meta=element('div',undefined,'meta');meta.append(element('span',view==='library'?'Saved':modeNames[row.mode]||'Prompt','badge'));if(row.status)meta.append(element('span',row.response_kind==='questions'?'Questions for you':row.status,'badge '+row.status));if(row.pinned)meta.append(element('span','★ Pinned','badge'));if(row.has_source)meta.append(element('span','📎 Source','badge'));if(row.response_kind==='pack')meta.append(element('span','Pack','badge dest'));if(row.destination&&row.destination!=='chat')meta.append(element('span',destShort[row.destination]||row.destination,'badge dest'));{const qa=qaOf(row);if(qa)meta.append(element('span',qa.passed?'QA ✓':'QA ⚠','badge '+(qa.passed?'success':'error')))}card.append(meta,element('h3',row.title),element('small',(view==='library'?'Saved library':row.source)+' · '+new Date(view==='library'?row.createdAt:row.created*1000).toLocaleDateString(undefined,{month:'short',day:'numeric',year:'numeric'}),'date'));card.onclick=()=>openDetail(row).catch(e=>toast(e.message));$('list').append(card)}
 if(!rows.length)$('list').append(element('div','No prompts here yet. Start a new prompt or try a different search.','empty'));
 $('count').textContent=total?`${offset+1}–${Math.min(offset+50,total)} of ${total}`:'0 prompts';$('prev').disabled=offset===0;$('next').disabled=offset+50>=total;
}
async function openDetail(row){current=view==='library'?{...row,id:'library-'+row.id,source:'Saved library',status:'saved',result:row.prompt,xml:'',optimizer_draft:'',library:true}:await api('/api/runs/'+row.id);tab='result';renderDetail();showResult()}
function qaOf(c){try{return c.qa?JSON.parse(c.qa):null}catch{return null}}
function renderQA(c,pending){const p=$('qa-panel');const qa=qaOf(c);p.replaceChildren();p.hidden=pending||!qa;if(p.hidden)return;
 const fixed=qa.revised&&qa.attempts.length>1&&qa.attempts[0].issues?qa.attempts[0].issues:[];
 p.className='qa'+(qa.passed?'':' warn');
 p.append(element('strong',qa.passed?(qa.revised?'✓ Quality check passed after one automatic revision':'✓ Quality check passed'):'⚠ Quality check flagged '+qa.issues.length+(qa.issues.length===1?' issue':' issues')+' — review before using'));
 p.append(element('span',qa.checks.filter(x=>x.passed).length+' of '+qa.checks.length+' checks passed','subtle'));
 const list=qa.passed?fixed:qa.issues;if(list.length){if(qa.passed)p.append(element('span','Fixed during revision:','subtle'));const ul=element('ul');for(const i of list)ul.append(element('li',i));p.append(ul)}}
function renderDetail(){
 if(!current)return;const c=current;$('detail-title').textContent=c.title;$('detail-source').textContent=c.source+(c.destination&&status&&status.destinations&&status.destinations[c.destination]?' · runs in '+status.destinations[c.destination]:'');
 const pending=['queued','running'].includes(c.status);const failed=['error','interrupted'].includes(c.status);
 const questions=JSON.parse(c.questions||'[]');
 $('run-state').className='notice'+(failed?' error':'');const waited=pending?' · '+Math.max(0,Math.round(Date.now()/1000-(c.started||c.created)))+'s':'';
 $('run-state').textContent=pending?(c.status==='queued'?'Queued for the local GPU.':({drafting:'Writing the prompt: optimizer draft, then the local model…',checking:'Checking quality…',revising:'Revising after the quality check…',planning:'Checking whether you asked for several prompts…',pack:packProgress(c)}[c.stage]||'Working on it…'))+waited:(failed?c.error:(c.library?'Saved library entry. No new MCP run was requested.':questions.length?'The assistant needs your input. Answer below to continue.':'Completed. The full XML is stored locally.'));
 renderQuestions(c,questions);renderQA(c,pending);renderAssumptions(c,pending);renderPack(c);$('reuse').hidden=questions.length>0;
 $('pin').hidden=$('archive').hidden=$('rename').hidden=!!c.library;$('pin').textContent=c.pinned?'★ Unpin':'☆ Pin';$('archive').textContent=c.archived?'Restore':'Archive';
 document.querySelectorAll('[data-tab]').forEach(b=>{b.classList.toggle('selected',b.dataset.tab===tab);b.disabled=!!c.library&&['xml','optimizer'].includes(b.dataset.tab)});
 $('evidence').replaceChildren();
 if(tab==='optimizer'){
   $('evidence').append(element('div','Tool: '+c.mode));
   if(c.execution_id){const a=element('a','View execution #'+c.execution_id+' in n8n ↗');a.href='http://127.0.0.1:5678/workflow/PromptAssistant20260909/executions/'+encodeURIComponent(c.execution_id);a.target='_blank';a.rel='noreferrer';$('evidence').append(a)}
   $('evidence').append(element('div',c.optimizer_ms?'Optimizer elapsed: '+(c.optimizer_ms/1000).toFixed(1)+' seconds':'Timing was not captured for this older run.'));
 }
 $('output').textContent=tab==='xml'?(c.xml||(failed?'No XML was produced because this run failed.':'')):tab==='optimizer'?(c.optimizer_draft||'No optimizer draft was captured for this entry.'):tab==='original'?(c.prompt+'\n\nRefinements: '+(c.requirements||'None specified')+(c.source_material?'\n\nSource material ('+c.source_material.length.toLocaleString()+' characters):\n'+c.source_material.slice(0,4000)+(c.source_material.length>4000?'\n…':''):'')):(c.result||(failed?'This run stopped before producing a result. Your original request is saved. Use Revise this to try again.':'Waiting for the complete result…'));
 $('elapsed').textContent=c.elapsed?'Total '+c.elapsed.toFixed(1)+' seconds · '+(c.execution_id?'execution #'+c.execution_id:'') : '';
 $('copy-prompt').disabled=!(c.library?c.prompt:c.result);$('copy').hidden=!!c.library;$('copy').textContent='Copy XML';$('copy').disabled=!c.library&&!c.xml;$('download').disabled=!c.xml;renderExtraTabs(c);
}
async function patch(body){current=await api('/api/runs/'+current.id,body);renderDetail();await load()}
$('pin').onclick=()=>{const was=current.pinned;patch({pinned:!was}).then(()=>toast(was?'Unpinned':'Pinned. It is also a slash command in Claude Code (prompt-workbench).')).catch(e=>toast(e.message))};$('archive').onclick=()=>patch({archived:!current.archived}).catch(e=>toast(e.message));$('rename').onclick=()=>{const title=prompt('Prompt title',current.title);if(title)patch({title}).catch(e=>toast(e.message))};
$('reuse').onclick=()=>{parent=current.library?null:current.id;$('prompt').value=current.result||current.prompt;$('requirements').value='';$('parent-label').textContent=parent?'Revision of '+current.title:'';selectMode('iterate-prompt');$('composer').scrollIntoView({behavior:'smooth'});$('requirements').focus()};
async function copy(text){await navigator.clipboard.writeText(text);toast('Copied')}
$('copy-prompt').onclick=()=>copy(current.library?current.prompt:current.result).catch(e=>toast(e.message));
$('copy').onclick=()=>copy(current.library?current.prompt:current.xml).catch(e=>toast(e.message));
$('download').onclick=()=>{const a=element('a');a.href=URL.createObjectURL(new Blob([current.xml],{type:'application/xml'}));a.download='prompt-'+current.id+'.xml';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000)};
function renderQuestions(c,questions){
 const panel=$('question-panel');panel.hidden=!questions.length||c.status!=='success';
 if(panel.hidden){panel.dataset.runId='';return}
 if(panel.dataset.runId===c.id)return;
 panel.dataset.runId=c.id;$('question-fields').replaceChildren();
 let saved=[];try{saved=JSON.parse(localStorage.getItem('answers-'+c.id)||'[]')}catch{}
 questions.forEach((q,i)=>{const label=element('label',q);const input=element('textarea');input.rows=2;input.required=true;input.id='answer-'+i;label.htmlFor=input.id;input.placeholder='Your answer, or “Use your best judgment”';input.value=saved[i]||'';input.oninput=()=>localStorage.setItem('answers-'+c.id,JSON.stringify([...$('question-fields').querySelectorAll('textarea')].map(t=>t.value)));$('question-fields').append(label,input)});
}
$('answer-form').onsubmit=async e=>{e.preventDefault();const rid=current.id;const answers=[...$('question-fields').querySelectorAll('textarea')].map(t=>t.value);$('send-answers').disabled=true;try{current=await api('/api/runs/'+rid+'/answers',{answers,request_id:crypto.randomUUID()});localStorage.removeItem('answers-'+rid);tab='result';renderDetail();await load()}catch(e){toast(e.message)}finally{$('send-answers').disabled=false}};
$('prompt-form').onsubmit=async e=>{e.preventDefault();$('run').disabled=true;try{const row=await api('/api/runs',{prompt:$('prompt').value,mode,requirements:$('requirements').value,source_material:$('source').value,destination:$('destination').value,pack:$('pack').value,quality:$('quality').hidden?undefined:$('quality').value,parent_id:parent,request_id:crypto.randomUUID()});current=row;tab='result';renderDetail();showResult();await load()}catch(e){toast(e.message)}finally{$('run').disabled=false}};
$('sync').onclick=async()=>{try{await api('/api/sync',{});await load();toast('History refreshed')}catch(e){toast(e.message)}};
async function health(){status=await api('/api/status');$('health').textContent=status.mcp.connected?'● MCP connected · '+status.mcp.tools.length+' tools':'○ MCP unavailable';$('health').classList.toggle('offline',!status.mcp.connected);// Limits and model names come from the server so the page cannot drift from the real configuration.
const k=n=>n>=1024&&n%1024===0?(n/1024)+'K':String(n);const limits=$('limits');if(limits)limits.textContent='Fast mode: '+k(status.context)+' context · '+k(status.generation)+' output tokens · no default word cap.';const stage=$('stage-model');if(stage)stage.textContent=(status.optimizer_model&&status.optimizer_model!==status.model?status.optimizer_model.split(':')[0]+' → '+status.model:status.model+' · both stages')+(status.thinking?'':' · thinking off')}
$('health').onclick=()=>{if(!status)return;$('mcp-summary').textContent=status.mcp.connected?'Connected to '+status.mcp.server.name+' using the MCP protocol.':status.mcp.error;$('tool-list').replaceChildren();for(const t of status.mcp.tools){const s=element('section');s.append(element('h3',t.name),element('p',t.description));$('tool-list').append(s)}$('mcp-info').showModal()};
setInterval(async()=>{try{if(current&&!current.library&&['queued','running'].includes(current.status)){current=await api('/api/runs/'+current.id);renderDetail();if(current.status==='success'||current.status==='error')await load()}}catch(e){toast(e.message)}},3000);
setInterval(()=>load().catch(()=>{}),15000);
setInterval(()=>{if(current&&!current.library&&['queued','running'].includes(current.status)&&!$('detail').hidden)renderDetail()},1000);
Promise.all([load(),health()]).catch(e=>toast(e.message));


// Source material: paste or attach. Files are converted to text by the local server.
let SOURCE_LIMIT=150000;   // replaced by the server's value from /api/status
api('/api/status').then(s=>{if(s.source_limit){SOURCE_LIMIT=s.source_limit;updateSource()}}).catch(()=>{});
const UNREADABLE=/https?:\/\/\S+|\bcanvas\b|\b(?:my|the|attached|these|those)\s+(?:files?|folders?|e-?mails?|resumes?|résumés?|documents?|attachments?|syllabus|notes)\b/i;
function updateSource(){const n=$('source').value.length;$('source-count').textContent=n?n.toLocaleString()+' / '+SOURCE_LIMIT.toLocaleString()+' characters'+(n>SOURCE_LIMIT?' — the rest will be cut':''):'';$('source-count').classList.toggle('over',n>SOURCE_LIMIT);
 const m=$('prompt').value.match(UNREADABLE);const w=$('ref-warning');const has=!!$('source').value.trim();w.hidden=!m;
 if(m){const ref='“'+m[0].slice(0,60)+'”';w.className='notice'+(has?'':' warn');w.textContent=has?'Your request mentions '+ref+'. The prompt will use the source material you attached; if '+ref+' is something else, attach that too.':'Your request mentions '+ref+'. The local model cannot open links or your files. Paste or attach that content under Source material, or it will ask you for it.'}}
$('source').addEventListener('input',updateSource);$('prompt').addEventListener('input',updateSource);
$('source-clear').onclick=()=>{$('source').value='';updateSource()};
async function attachFiles(files){for(const f of files)await attachFile(f)}
$('source-file').onchange=e=>{const fs=[...e.target.files];e.target.value='';attachFiles(fs)};
// Same list as the server's CONVERT_TYPES: refuse other files here instead of uploading them first.
const SOURCE_TYPES=['.pdf','.docx','.pptx','.xlsx','.html','.htm','.txt','.md','.csv','.json','.png','.jpg','.jpeg','.webp','.gif','.bmp','.tif','.tiff'];
async function attachFile(f){
 const ext=(f.name.match(/\.[^.]+$/)||[''])[0].toLowerCase();
 if(!SOURCE_TYPES.includes(ext)){toast(f.name+': unsupported file type. Use '+SOURCE_TYPES.join(', '));return}
 if(f.size>40*1024*1024){toast(f.name+' is larger than 40 MB');return}
 toast('Reading '+f.name+'…');
 try{const data=await new Promise((ok,no)=>{const r=new FileReader();r.onload=()=>ok(String(r.result).split(',')[1]||'');r.onerror=()=>no(new Error('Could not read the file'));r.readAsDataURL(f)});
  const res=await api('/api/convert',{filename:f.name,data});const scanned=res.ocr_pages||[];
  if(res.text){const cur=$('source').value.trim();
   $('source').value=(cur?cur+'\n\n':'')+'--- '+f.name+' ---\n'+res.text;$('source-box').open=true;updateSource();
   toast(f.name+': '+res.chars.toLocaleString()+' characters'+(res.truncated?' (cut to '+res.limit.toLocaleString()+')':'')+(scanned.length?'; reading '+scanned.length+' scanned page'+(scanned.length>1?'s':'')+'…':''))}
  if(scanned.length)await ocrPages(f,data,res)}
 catch(err){toast(f.name+': '+err.message)}}
// Drop files anywhere on the composer.
{const zone=$('prompt-form');zone.addEventListener('dragover',e=>{if([...e.dataTransfer.types].includes('Files')){e.preventDefault();zone.classList.add('drop-active')}});
 zone.addEventListener('dragleave',e=>{if(e.target===zone)zone.classList.remove('drop-active')});
 zone.addEventListener('drop',e=>{zone.classList.remove('drop-active');if(!e.dataTransfer.files.length)return;e.preventDefault();attachFiles([...e.dataTransfer.files])})}
// Ctrl/Cmd+Enter runs the prompt.
$('prompt').addEventListener('keydown',e=>{if(e.key==='Enter'&&(e.ctrlKey||e.metaKey)){e.preventDefault();$('prompt-form').requestSubmit()}});
// Use the workbench from Claude Code (MCP).
$('use-mcp').onclick=async()=>{let s=status;try{s=await api('/api/status')}catch{}
 const cmd='claude mcp add --transport http prompt-workbench '+(s&&s.workbench_mcp?s.workbench_mcp.url:'http://127.0.0.1:8789/mcp');
 let d=document.getElementById('mcp-dialog');if(!d){d=document.createElement('dialog');d.id='mcp-dialog';document.body.append(d)}
 d.replaceChildren();const head=element('div',undefined,'dialog-head');const t=element('div');t.append(element('span','MCP SERVER','eyebrow'),element('h2','Use the workbench from Claude Code'));
 const x=element('button','×');x.onclick=()=>d.close();head.append(t,x);const body=element('div',undefined,'dialog-body');
 body.append(element('p','Run this once in a terminal. Claude Code can then write QA-checked prompts through this workbench, and those runs appear in your history marked MCP.','subtle'));
 const pre=element('pre',cmd);const copyBtn=element('button','Copy command','primary');copyBtn.onclick=()=>copy(cmd).then(()=>toast('Copied')).catch(e=>toast(e.message));
 body.append(pre,copyBtn,element('h3','Tools'));const ul=element('ul');for(const n of (s&&s.workbench_mcp?s.workbench_mcp.tools:[]))ul.append(element('li',n));body.append(ul);
 body.append(element('p','Your saved library prompts and any prompts you pin here also appear in Claude Code as slash commands (type / and look for prompt-workbench); their {{placeholders}} become arguments.','subtle'));
 body.append(element('p','Local only: it answers on 127.0.0.1 and refuses web pages from other sites.','subtle'));d.append(head,body);loadConnectors(body);d.showModal()};
updateSource();

// One-shot: destination, defaults, assumptions and the questions it would have asked.
const destShort={'agent':'Agent','notebooklm-chat':'NotebookLM','notebooklm-instructions':'NotebookLM setup','gpt-instructions':'Instructions'};
function listOf(v){try{const a=JSON.parse(v||'[]');return Array.isArray(a)?a:[]}catch{return[]}}
async function fillDestinations(){try{const s=status||await api('/api/status');const sel=$('destination');if(sel.options.length>1)return;
 for(const [k,label] of Object.entries(s.destinations||{}))sel.append(new Option(label,k))}catch{}}
let destTimer;function detectDest(){clearTimeout(destTimer);destTimer=setTimeout(async()=>{const hint=$('dest-hint');
 if($('destination').value!=='auto'||!$('prompt').value.trim()){hint.textContent='';return}
 try{const d=await api('/api/destination',{prompt:$('prompt').value,requirements:$('requirements').value});hint.textContent='Detected: '+d.label}catch{hint.textContent=''}},300)}
$('prompt').addEventListener('input',detectDest);$('requirements').addEventListener('input',detectDest);$('destination').addEventListener('change',detectDest);
async function loadDefaults(){try{const s=await api('/api/settings');$('qa-default').checked=!!s.qa_default;$('defaults-text').value=s.defaults_text||''}catch{}}
$('save-defaults').onclick=async()=>{try{await api('/api/settings',{qa_default:$('qa-default').checked,defaults_text:$('defaults-text').value});$('defaults-state').textContent='Saved. Applies to new prompts.';toast('Defaults saved')}catch(e){toast(e.message)}};
function renderAssumptions(c,pending){
 const done=!pending&&c.status==='success'&&c.response_kind==='prompt'&&!c.library;
 const as=done?listOf(c.assumptions):[];const ap=$('assumptions-panel');ap.hidden=!as.length;$('assumption-list').replaceChildren(...as.map(a=>element('li',a)));
 const qs=done?listOf(c.open_questions):[];const op=$('open-panel');op.hidden=!qs.length;
 if(op.hidden){op.dataset.runId='';return}
 if(op.dataset.runId===c.id)return;op.dataset.runId=c.id;$('open-fields').replaceChildren();
 qs.forEach((q,i)=>{const label=element('label',q);const input=element('textarea');input.rows=1;input.id='open-'+i;label.htmlFor=input.id;input.placeholder='Optional';$('open-fields').append(label,input)})}
$('adjust').onclick=()=>{const as=listOf(current.assumptions);parent=current.id;$('prompt').value=current.result||current.prompt;
 $('requirements').value='Change these assumptions:\n'+as.map(a=>'- '+a+' -> ').join('\n');$('requirements').closest('details').open=true;
 $('parent-label').textContent='Revision of '+current.title;selectMode('iterate-prompt');$('composer').scrollIntoView({behavior:'smooth'});$('requirements').focus()};
$('open-form').onsubmit=async e=>{e.preventDefault();const answers=[...$('open-fields').querySelectorAll('textarea')].map(t=>t.value);
 if(!answers.some(a=>a.trim())){toast('Answer at least one question, or just use the prompt as it is');return}
 $('send-open').disabled=true;try{current=await api('/api/runs/'+current.id+'/answers',{answers,request_id:crypto.randomUUID()});tab='result';renderDetail();await load()}catch(err){toast(err.message)}finally{$('send-open').disabled=false}};
fillDestinations();loadDefaults();

// Two-pane workspace: the result lives next to the composer instead of in a dialog.
function showResult(){$('result-empty').hidden=true;$('detail').hidden=false;
 if(window.matchMedia('(max-width: 1100px)').matches)$('detail').scrollIntoView({behavior:'smooth',block:'start'})}
$('clear-result').onclick=()=>{$('detail').hidden=true;$('result-empty').hidden=false;current=null};
document.querySelectorAll('[data-filter]').forEach(b=>b.onclick=()=>{filter=b.dataset.filter;offset=0;
 document.querySelectorAll('[data-filter]').forEach(x=>x.classList.toggle('on',x===b));load().catch(e=>toast(e.message))});
// Alt+C copies the prompt (Ctrl+Shift+C is the browser's inspector).
document.addEventListener('keydown',e=>{if(e.altKey&&!e.ctrlKey&&(e.key==='c'||e.key==='C')&&current&&(current.result||current.prompt)){e.preventDefault();$('copy-prompt').click()}});
// Theme: follows the system until chosen; the choice is remembered on this computer.
$('theme').onclick=()=>{const dark=document.documentElement.dataset.theme!=='dark';document.documentElement.dataset.theme=dark?'dark':'light';
 try{localStorage.setItem('theme',dark?'dark':'light')}catch{}};

// Prompt packs: several titled prompts from one request, each with its own QA.
function packProgress(c){const items=c.pack_items||[];const done=items.filter(i=>!['queued','running'].includes(i.status)).length;
 return items.length?'Writing the prompts: '+done+' of '+items.length+' done…':'Writing the prompts…'}
function renderPack(c){const panel=$('pack-panel');const items=c.pack_items||[];panel.hidden=!items.length;if(panel.hidden)return;
 $('pack-list').replaceChildren(...items.map(i=>{const row=element('div',undefined,'pack-item');
  const head=element('div',undefined,'pack-head');head.append(element('strong',i.title));
  const st=['queued','running'].includes(i.status)?element('span',i.status==='running'?'writing…':'waiting','badge running'):
   element('span',i.status!=='success'?'no prompt':(i.qa_passed?'QA ✓':'QA ⚠'),'badge '+(i.status==='success'&&i.qa_passed?'success':'error'));
  head.append(st);
  if(i.status==='success'&&i.result){const cp=element('button','Copy');cp.type='button';cp.onclick=()=>copy(i.result).catch(e=>toast(e.message));
   const op=element('button','Open');op.type='button';op.onclick=()=>openDetail({id:i.id}).catch(e=>toast(e.message));head.append(cp,op)}
  row.append(head);if(i.result)row.append(element('p',i.result.slice(0,180)+(i.result.length>180?'…':''),'subtle'));return row}))}

// Try it, Changes and connection activity (2026-10-21).
const PH_RE=/\{\{\s*([\w .-]+?)\s*\}\}/g;
function renderExtraTabs(c){
 const tryBtn=document.querySelector('[data-tab=try]'),diffBtn=document.querySelector('[data-tab=diff]');
 tryBtn.hidden=!!c.library||c.response_kind!=='prompt'||c.status!=='success';
 diffBtn.hidden=!!c.library||!c.parent_id||c.status!=='success';
 if((tab==='try'&&tryBtn.hidden)||(tab==='diff'&&diffBtn.hidden))tab='result';
 if(tab==='versions'&&(c.library||!c.id))tab='result';
 const extra=tab==='try'||tab==='diff'||tab==='versions';$('output').hidden=extra;$('try-panel').hidden=tab!=='try';$('diff-panel').hidden=tab!=='diff';
 $('versions-panel').hidden=tab!=='versions';
 if(tab==='try')buildTry(c);if(tab==='diff')buildDiff(c).catch(e=>toast(e.message));if(tab==='versions')buildVersions(c).catch(e=>toast(e.message));
 showVersionsTab(c);
}
async function parentPrompt(c){if(!c.parent_id)return null;const p=await api('/api/runs/'+c.parent_id);return p.response_kind==='prompt'&&p.result?p:null}
function buildTry(c){
 const panel=$('try-panel');if(panel.dataset.runId===c.id)return;panel.dataset.runId=c.id;
 const names=[...new Set([...(c.result||'').matchAll(PH_RE)].map(m=>m[1]))];
 $('try-fields').replaceChildren(...names.flatMap((n,i)=>{const l=element('label','{{'+n+'}}');const t=element('textarea');t.rows=2;t.id='try-'+i;t.dataset.name=n;l.htmlFor=t.id;return [l,t]}));
 if(!names.length)$('try-fields').append(element('p','This prompt has no {{variables}}; it runs as written.','subtle'));
 $('try-out').replaceChildren();$('try-compare').checked=false;$('try-compare-wrap').hidden=true;$('try-save-row').hidden=!names.length;$('try-saved').replaceChildren();if(names.length)loadSaved(c);
 parentPrompt(c).then(p=>{if(p&&current&&current.id===c.id){$('try-compare-wrap').hidden=false;panel.dataset.parentId=p.id}}).catch(()=>{});
}
function tryBox(title,res){const box=element('div',undefined,'try-box');box.append(element('strong',title));
 if(res.error){box.append(element('p',res.error,'subtle'))}else{box.append(element('pre',res.output||'(empty answer)'));
  box.append(element('span',(res.ms/1000).toFixed(1)+' s on '+res.model+(res.unfilled.length?' · sent unfilled: '+res.unfilled.join(', '):''),'subtle'))}
 return box}
$('try-form').onsubmit=async e=>{e.preventDefault();const c=current;if(!c)return;const values={};
 $('try-fields').querySelectorAll('textarea').forEach(t=>{if(t.value.trim())values[t.dataset.name]=t.value});
 const jobs=tryJobs(c,values);
 $('try-run').disabled=true;$('try-out').replaceChildren(element('p','Running on the local model…','subtle'));
 try{const out=[],res=[];for(const [title,body] of jobs){let r;try{r=await api('/api/try',body)}catch(err){r={error:err.message}}res.push(r);out.push(tryBox(title,r))}
  $('try-out').replaceChildren(...out);if(res.length===2)$('try-out').append(judgeBox(c,values,res[0],res[1]))}finally{$('try-run').disabled=false}};
// Word-level differences between the previous prompt and this one (longest common subsequence).
function wordDiff(a,b){const A=a.split(/(\s+)/),B=b.split(/(\s+)/);const n=A.length,m=B.length;
 if(n*m>4e6)return null;const L=Array.from({length:n+1},()=>new Uint16Array(m+1));
 for(let i=n-1;i>=0;i--)for(let j=m-1;j>=0;j--)L[i][j]=A[i]===B[j]?L[i+1][j+1]+1:Math.max(L[i+1][j],L[i][j+1]);
 const out=[];let i=0,j=0;while(i<n&&j<m){if(A[i]===B[j]){out.push([0,A[i]]);i++;j++}else if(L[i+1][j]>=L[i][j+1])out.push([-1,A[i++]]);else out.push([1,B[j++]])}
 while(i<n)out.push([-1,A[i++]]);while(j<m)out.push([1,B[j++]]);return out}
async function buildDiff(c){const panel=$('diff-panel');if(panel.dataset.runId===c.id)return;panel.dataset.runId=c.id;
 const view=$('diff-view');view.replaceChildren();const p=await parentPrompt(c);
 if(!p){$('diff-note').textContent='The run before this one asked questions instead of writing a prompt, so there is nothing to compare.';return}
 const d=wordDiff(p.result,c.result||'');if(!d){$('diff-note').textContent='Too long to compare word by word.';return}
 const added=d.filter(x=>x[0]===1&&x[1].trim()).length,removed=d.filter(x=>x[0]===-1&&x[1].trim()).length;
 $('diff-note').textContent='Compared with the previous version ('+p.id.slice(0,8)+'): '+added+' words added, '+removed+' removed.';
 view.append(...d.map(([k,t])=>k===0?document.createTextNode(t):element(k>0?'ins':'del',t)))}
async function loadConnectors(body){try{const s=await api('/api/connectors');const clients=s.clients.filter(x=>x.client!=='test');
 body.append(element('h3','Connection activity (last '+s.days+' days)'));
 if(!clients.length){body.append(element('p','No calls from Claude yet.','subtle'));return}
 const t=element('table');t.className='conn-table';const hr=element('tr');['Client','Calls','Succeeded','Typical time','Last used','Last problem'].forEach(h=>hr.append(element('th',h)));t.append(hr);
 for(const x of clients){const r=element('tr');const when=v=>v?new Date(v*1000).toLocaleString(undefined,{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'}):'—';
  [x.client,String(x.calls),x.success_rate==null?'—':Math.round(100*x.success_rate)+'%',x.median_ms==null?'—':(x.median_ms/1000).toFixed(1)+' s',when(x.last_seen),x.last_error?x.last_error.slice(0,90):'none'].forEach(v=>r.append(element('td',v)));t.append(r)}
 body.append(t)}catch(e){body.append(element('p','Connection activity unavailable: '+e.message,'subtle'))}}

// Saved test inputs (2026-10-21): named sets of variable values kept for a prompt and all its
// revisions, so the same inputs can be re-run after every change.
let savedInputs=[];
function tryJobs(c,values){const jobs=[['This version',{run_id:c.id,values}]];
 if($('try-compare').checked&&$('try-panel').dataset.parentId)jobs.push(['Previous version',{run_id:$('try-panel').dataset.parentId,values}]);return jobs}
function tryValues(){const v={};$('try-fields').querySelectorAll('textarea').forEach(t=>{if(t.value.trim())v[t.dataset.name]=t.value});return v}
async function loadSaved(c){try{const r=await api('/api/try-inputs?run_id='+encodeURIComponent(c.id));if(current&&current.id===c.id)renderSaved(c,r.rows)}catch(e){}}
function renderSaved(c,rows){savedInputs=rows;const box=$('try-saved');box.replaceChildren();if(!rows.length)return;
 box.append(element('span','Saved inputs:','subtle'));
 for(const x of rows){const chip=element('span',undefined,'chip');const use=element('button',x.name);use.type='button';use.title='Fill in these values';
  use.onclick=()=>{$('try-fields').querySelectorAll('textarea').forEach(t=>{t.value=x.values[t.dataset.name]||''});$('try-name').value=x.name};
  const del=element('button','×');del.type='button';del.title='Delete '+x.name;del.setAttribute('aria-label','Delete '+x.name);
  del.onclick=async()=>{try{renderSaved(c,(await api('/api/try-inputs',{run_id:c.id,delete:x.id})).rows)}catch(e){toast(e.message)}};
  chip.append(use,del);box.append(chip)}
 const all=element('button','Run all saved ('+rows.length+')');all.type='button';all.onclick=()=>runAllSaved(c);box.append(all)}
$('try-save').onclick=async()=>{const c=current;if(!c)return;
 try{renderSaved(c,(await api('/api/try-inputs',{run_id:c.id,name:$('try-name').value,values:tryValues()})).rows);toast('Inputs saved')}catch(e){toast(e.message)}};
async function runAllSaved(c){if(!savedInputs.length)return;const out=$('try-out');out.replaceChildren();$('try-run').disabled=true;
 try{for(const x of savedInputs){const group=element('div',undefined,'try-group');group.append(element('h4',x.name));const grid=element('div',undefined,'try-grid');group.append(grid);out.append(group);
  grid.append(element('p','Running on the local model…','subtle'));const boxes=[];
  const res=[];for(const [title,body] of tryJobs(c,x.values)){let r;try{r=await api('/api/try',body)}catch(err){r={error:err.message}}res.push(r);boxes.push(tryBox(title,r))}
  grid.replaceChildren(...boxes);if(res.length===2)group.append(judgeBox(c,x.values,res[0],res[1]))}}finally{$('try-run').disabled=false}}

// Scanned pages and pictures (2026-10-25): read one page at a time by the local vision model, with
// progress and a Stop button. The text is labelled as OCR so names and numbers get checked.
let ocrStop=false;$('ocr-stop').onclick=()=>{ocrStop=true;$('ocr-text').textContent='Stopping after this page…'};
async function ocrPages(f,data,res){const pages=res.ocr_pages,paged=/\.pdf$/i.test(f.name)||res.pages>1,parts=[],failed=[];ocrStop=false;
 $('source-box').open=true;$('ocr-status').hidden=false;const t0=Date.now();
 try{for(let i=0;i<pages.length&&!ocrStop;i++){
   const left=i?Math.round((Date.now()-t0)/i*(pages.length-i)/1000):null;
   $('ocr-text').textContent='Reading '+f.name+(pages.length>1?': scanned page '+(i+1)+' of '+pages.length:'')+' with the local model'+(left?' (about '+(left>90?Math.round(left/60)+' min':left+' s')+' left)':'…');
   try{const r=await api('/api/ocr',{filename:f.name,data,page:pages[i]});if(r.text)parts.push((paged?'[Page '+(pages[i]+1)+', read by OCR]\n':'')+r.text)}
   catch(err){failed.push((paged?'page '+(pages[i]+1)+': ':'')+err.message)}}
  const done=parts.length,problem=failed.length?' Could not read '+failed.join('; ')+'.':'';
  if(!done){toast(f.name+': '+(problem?problem.trim():'no text found on the scanned page'+(pages.length>1?'s':'')));return}
  const cur=$('source').value.trim(),label=paged&&res.text?' (scanned pages)':'';
  $('source').value=(cur?cur+'\n\n':'')+'--- '+f.name+label+', read by OCR: check names and numbers ---\n'+parts.join('\n\n');updateSource();
  toast(f.name+': read '+done+' scanned page'+(done>1?'s':'')+(ocrStop?' (stopped early)':'')+(res.ocr_skipped?'; '+res.ocr_skipped+' more pages were not read (limit '+pages.length+')':'')+problem)}
 finally{$('ocr-status').hidden=true;ocrStop=false}}

// Installable app window: the browser offers "Install" once a service worker is registered.
if('serviceWorker' in navigator)navigator.serviceWorker.register('/sw.js').catch(()=>{});

// Phone: Paste button (iOS asks before reading the clipboard) and a refresh when the app comes back
// to the foreground, since iOS pauses background apps (2026-10-28).
$('paste').onclick=async()=>{try{const t=await navigator.clipboard.readText();if(!t.trim()){toast('The clipboard is empty');return}
 const p=$('prompt');p.value=(p.value.trim()?p.value.trimEnd()+'\n\n':'')+t;p.dispatchEvent(new Event('input'));p.focus()}
 catch(e){toast('Paste was blocked: press and hold in the box, then choose Paste')}};
document.addEventListener('visibilitychange',()=>{if(!document.hidden)load().catch(()=>{})});

// Phone tab bar (2026-10-28): Write jumps to the composer; the list tabs show their list.
const phone=()=>window.matchMedia('(max-width: 760px)').matches;
$('tab-write').onclick=()=>{document.querySelectorAll('[data-view]').forEach(x=>x.classList.remove('active'));$('tab-write').classList.add('active');$('new').click()};
document.querySelectorAll('[data-view]').forEach(b=>b.addEventListener('click',()=>{$('tab-write').classList.remove('active');
 if(phone())document.querySelector('.collection').scrollIntoView({behavior:'smooth',block:'start'})}));

// Local/API model switch (2026-10-31): shown only when an API key is configured; the choice is
// remembered on this device, and the status pill shows this month's API spend against the cap.
function applyQuality(st){const on=!!(st&&st.api_key);$('quality').hidden=!on;$('quality-label').hidden=!on;if(!on)return;
 try{const q=localStorage.getItem('workbench-quality');if(q)$('quality').value=q}catch(e){}
 const sp=st.api_spend;if(sp&&!$('health').textContent.includes('API $'))$('health').textContent+=' · API $'+sp.month.toFixed(2)+' of $'+sp.cap.toFixed(0)}
$('quality').onchange=()=>{try{localStorage.setItem('workbench-quality',$('quality').value)}catch(e){}};
(async()=>{for(let i=0;i<20&&!status;i++)await new Promise(r=>setTimeout(r,500));applyQuality(status)})();

// Try it judge (2026-11-01): after this version and the previous one answer the same inputs, the
// local model says which better does what was first asked. It judges in both orders and only names a
// winner when both agree, so a tie means "too close to call".
function judgeBox(c,values,a,b){const box=element('div',undefined,'judge-box');
 if(a.error||b.error||!a.output||!b.output){box.append(element('span','No comparison: one of the versions did not answer.','subtle'));return box}
 box.append(element('span','Comparing the two answers against your original request…','subtle'));
 api('/api/try/judge',{run_id:c.id,values,answer_a:a.output,answer_b:b.output}).then(j=>{
  const head={a:'This version is better',b:'The previous version is better',tie:'Too close to call'}[j.winner]||'Too close to call';
  box.replaceChildren(element('strong',head));
  if(j.reasons&&j.reasons.length){const ul=element('ul');j.reasons.forEach(r=>ul.append(element('li',r)));box.append(ul)}
  box.append(element('span','Judged in both orders by '+j.model+' · '+(j.ms/1000).toFixed(1)+' s'+(j.winner==='tie'&&!j.agreed?' · the two orders disagreed':''),'subtle'));
  box.dataset.winner=j.winner}).catch(e=>box.replaceChildren(element('span','No comparison: '+e.message,'subtle')));
 return box}

// Versions tab (2026-11-02): every version of this prompt (revisions and answered questions), oldest
// first, with its QA result. Open one, or compare its text with the version shown now.
const versionCache={};
async function versionsOf(c){if(!versionCache[c.id])versionCache[c.id]=(await api('/api/runs/'+c.id+'/versions')).versions;return versionCache[c.id]}
function showVersionsTab(c){const b=document.querySelector('[data-tab=versions]');if(c.library||!c.id){b.hidden=true;return}
 versionsOf(c).then(v=>{if(current&&current.id===c.id){b.hidden=v.length<2;b.textContent='Versions ('+v.length+')'}}).catch(()=>{b.hidden=true})}
async function buildVersions(c){const panel=$('versions-panel');if(panel.dataset.runId===c.id)return;panel.dataset.runId=c.id;
 const v=await versionsOf(c),list=$('versions-list');list.replaceChildren();$('versions-diff').hidden=true;$('versions-diff-note').textContent='';
 $('versions-note').textContent=v.length+' versions of this prompt, oldest first. The one you are looking at is marked.';
 v.forEach((x,i)=>{const li=element('li',undefined,x.id===c.id?'here':'');
  const qa=x.qa_passed===true?'QA ✓':x.qa_passed===false?'QA ⚠':'';const kind=x.response_kind==='questions'?'asked questions':x.response_kind==='message'?'message':x.status;
  li.append(element('strong','v'+(i+1)),element('span',new Date(x.created*1000).toLocaleString(undefined,{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'})+' · '+kind+(qa?' · '+qa:'')+(x.pinned?' · ★ pinned':''),'subtle'));
  if(x.id!==c.id){const open=element('button','Open');open.type='button';open.onclick=async()=>{current=await api('/api/runs/'+x.id);tab='result';renderDetail()};li.append(open);
   if(x.status==='success'&&x.response_kind==='prompt'&&c.result){const cmp=element('button','Changes vs this');cmp.type='button';cmp.onclick=()=>compareVersion(c,x,i+1);li.append(cmp)}}
  else li.append(element('span','this version','badge'));
  list.append(li)})}
async function compareVersion(c,x,n){const other=await api('/api/runs/'+x.id);const d=wordDiff(other.result||'',c.result||'');const view=$('versions-diff');
 if(!d){$('versions-diff-note').textContent='Too long to compare word by word.';view.hidden=true;return}
 const added=d.filter(y=>y[0]===1&&y[1].trim()).length,removed=d.filter(y=>y[0]===-1&&y[1].trim()).length;
 $('versions-diff-note').textContent='From v'+n+' to the version shown: '+added+' words added, '+removed+' removed.';
 view.replaceChildren(...d.map(([k,t])=>k===0?document.createTextNode(t):element(k>0?'ins':'del',t)));view.hidden=false}

// Chat tab (2026-11-10): the local chatbot (chat/web.py on this PC) inside the workbench. The server
// passes /api/chat through, so the same page and tailnet address work on the phone. Commands such as
// /use, /prompt, /run and /promptify connect the chat to your prompts.
(function(){
 const log=$('chat-log'),input=$('chat-input'),send=$('chat-send');let session='',loaded=false;
 try{session=localStorage.getItem('wb-chat-session')||''}catch{}
 function fresh(){session=Date.now().toString(36);try{localStorage.setItem('wb-chat-session',session)}catch{};log.replaceChildren(element('p','Ask about your indexed documents, or type /help for commands. /use chats as one of your saved prompts; /prompt then /run uses a new one; /promptify turns this conversation into a prompt.','empty-chat'))}
 function add(role,text,meta){log.querySelector('.empty-chat')?.remove();const d=element('div',text,'msg '+role);
  if(meta)d.append(element('div',meta,'meta'));
  if(role==='assistant'&&text&&text.length>40){const a=element('div',undefined,'msg-actions');const c=element('button','Copy');c.type='button';c.onclick=()=>navigator.clipboard.writeText(text).then(()=>toast('Copied'));a.append(c);d.append(a)}
  log.append(d);log.scrollTop=log.scrollHeight;return d}
 async function history(){if(!session){fresh();return}
  try{const rows=await api('/api/chat/history?session='+encodeURIComponent(session));log.replaceChildren();if(!rows.length)fresh();rows.forEach(r=>add(r.role,r.content))}
  catch(e){log.replaceChildren(element('p',e.message,'empty-chat'))}}
 function show(on){document.body.classList.toggle('chatting',on);$('chat-view').hidden=!on;$('tab-chat').classList.toggle('active',on);
  if(on){document.querySelectorAll('[data-view]').forEach(x=>x.classList.remove('active'));$('tab-write').classList.remove('active');if(!loaded){loaded=true;history()}setTimeout(()=>input.focus(),0)}}
 $('tab-chat').onclick=()=>show(true);
 document.querySelectorAll('[data-view]').forEach(b=>b.addEventListener('click',()=>show(false)));
 $('tab-write').addEventListener('click',()=>show(false));$('new').addEventListener('click',()=>show(false));
 $('chat-new').onclick=()=>{fresh();input.focus()};
 document.querySelectorAll('#chat-chips [data-cmd]').forEach(b=>b.onclick=()=>{const c=b.dataset.cmd;if(c.endsWith(' ')){input.value=c;input.focus()}else{input.value=c;go()}});
 input.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();go()}});
 $('chat-form').onsubmit=e=>{e.preventDefault();go()};
 async function go(){const text=input.value.trim();if(!text||send.disabled)return;if(!session)fresh();
  add('user',text);input.value='';send.disabled=true;const wait=add('assistant','Thinking…');const t0=Date.now();
  const tick=setInterval(()=>{wait.firstChild.textContent='Thinking… '+Math.round((Date.now()-t0)/1000)+' s'},1000);
  try{const d=await api('/api/chat',{message:text,session});wait.remove();
   add('assistant',d.reply,(d.as?'as “'+d.as+'” · ':'')+((d.sources||[]).length?d.sources.join('  ')+' · ':'')+d.model+' · '+d.seconds+' s');
   if(/^\/(save|prompt|promptify)\b/.test(text))load().catch(()=>{})}
  catch(e){wait.remove();add('assistant','Error: '+e.message)}
  finally{clearInterval(tick);send.disabled=false;input.focus()}}
 api('/api/status').then(s=>{$('tab-chat').hidden=!s.chat;if(s.chat&&location.hash==='#chat')show(true)}).catch(()=>{});
})();
