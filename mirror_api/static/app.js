'use strict';
let rows=[], active=null, remoteChecked=false, ownerGitRepos=new Set(), csrf=null, checkController=null;
const $=id=>document.getElementById(id);
const node=(tag,text,cls)=>{const e=document.createElement(tag);if(cls)e.className=cls;if(text!==undefined)e.textContent=String(text);return e};
const add=(parent,tag,text,cls)=>{const e=node(tag,text,cls);parent.append(e);return e};
const show=v=>v===null||v===undefined?'Неизвестно':String(v);
function info(parent,title,value,cls){const row=add(parent,'div',undefined,'row');add(row,'span',title,'label');add(row,'span',show(value),'value '+(cls||''));}
function githubLink(parent,label,url){
  if(typeof url!=='string'||!/^https:\/\/github\.com\/AlexPastukhh\/[A-Za-z0-9_.-]+\/(?:pull|actions\/runs)\/[0-9]+$/.test(url))return;
  const link=add(parent,'a',label,'external');link.href=url;link.target='_blank';link.rel='noopener noreferrer';
}
function prCiDetails(panel,worktree){
  const pr=worktree.pullRequest||{status:'UNVERIFIED'},ci=worktree.ci||{status:'UNVERIFIED'};
  info(panel,'PR',pr.status);
  if(pr.data){
    info(panel,'PR №',pr.data.number);info(panel,'Заголовок',pr.data.title);
    info(panel,'PR head SHA',pr.data.head?.sha);info(panel,'PR ветка опубликована',pr.headMatchesPublished);
    githubLink(panel,'Открыть PR',pr.data.url);
  }
  info(panel,'CI для PR head SHA',ci.headSha);
  info(panel,'Состояние наблюдаемых CI',ci.status,ci.status==='OBSERVED_FAILURE'?'bad':'');
  info(panel,'Обязательный CI подтверждён',ci.requiredGateVerified===true?'Да':'Нет');
  for(const run of ci.workflowRuns||[]){
    const detail=add(panel,'div',undefined,'cirow');
    add(detail,'strong',run.name+' · '+run.status+' / '+(run.conclusion||'—'));
    info(detail,'Run / attempt',run.runNumber+' / '+run.runAttempt);
    info(detail,'SHA',run.headSha);githubLink(detail,'Открыть Actions',run.url);
  }
  for(const check of ci.checkRuns||[]){
    info(panel,'Check '+check.name,check.status+' / '+(check.conclusion||'—'));
  }
  if(ci.checksTruncated)add(panel,'p','Часть проверок GitHub недоступна в ответе.','warn');
}
function status(row){const w=row.windows,g=row.github;if(w.error)return w.error;if(w.clean===false)return 'Windows: изменения';if(g.error)return 'GitHub: '+g.error;if(w.relation==='equal'&&w.branch==='main'&&w.clean===true)return 'Актуален';if(w.relation==='behind')return 'Windows отстаёт';if(w.relation==='ahead')return 'Windows впереди';if(w.relation==='diverged')return 'Истории разошлись';return 'Направление не установлено';}
function render(){const repoList=$('repos');repoList.replaceChildren();rows.forEach(r=>{const button=add(repoList,'button',r.repoId+(r.repoId===active?'  •':''),'');if(r.repoId===active)button.classList.add('selected');button.onclick=()=>{active=r.repoId;render()}});const total=rows.length;const dirty=rows.filter(r=>r.docker.worktrees.some(w=>w.clean===false)).length;const behind=rows.filter(r=>r.windows.relation==='behind').length;$('summary').replaceChildren();for(const [title,value] of [['Репозитории',total],['Docker с изменениями',dirty],['Windows отстаёт',behind]]){const s=add($('summary'),'div',undefined,'stat');add(s,'span',title,'muted');add(s,'strong',value)}const target=rows.find(r=>r.repoId===active);const d=$('details');d.replaceChildren();if(!target){add(d,'p','Выберите репозиторий');return}add(d,'h2',target.repoId);add(d,'p',status(target),target.windows.relation==='equal'?'good':'warn');add(d,'h3','GitHub main');info(d,'HEAD',target.github.head);info(d,'Проверка',target.github.error||'OK');info(d,'Время',target.github.checkedAt);
const gh=target.github;
info(d,'PR',Array.isArray(gh.pullRequests)?gh.pullRequests.length+' найдено':'Не проверены');
info(d,'GitHub Actions',Array.isArray(gh.workflowRuns)?gh.workflowRuns.length+' запусков':'Не проверены');
info(d,'Обязательный CI',gh.requiredCI);
if(Object.keys(gh.readErrors||{}).length)info(d,'Gateway PR/CI',Object.entries(gh.readErrors).map(([key,value])=>key+': '+value).join(' · '),'warn');
if(Object.values(gh.truncated||{}).some(Boolean))info(d,'Полнота PR/CI','Данные постранично ограничены; отсутствие PR или CI не доказано','warn');
if(Array.isArray(gh.pullRequests)&&gh.pullRequests.length){
 const prs=add(d,'div',undefined,'pr-overview');
 for(const pr of gh.pullRequests.slice(0,10)){
   const row=add(prs,'div',undefined,'cirow');
   add(row,'strong','#'+pr.number+' · '+pr.title);
   add(row,'span',' · '+(pr.merged?'MERGED':pr.state==='closed'?(pr.mergeVerified?'CLOSED':'CLOSED/MERGED не подтверждено'):pr.draft?'DRAFT':pr.state)+' · '+pr.head.ref,'muted');
   githubLink(row,' PR',pr.url);
 }
}

add(d,'h3','Windows primary');const w=target.windows;for(const [k,v] of [['Ветка',w.branch],['HEAD',w.head],['Origin',w.origin],['Origin соответствует',w.originValid],['Чистый',w.clean],['Изменено файлов',w.changedCount],['Отношение к GitHub',w.relation],['Ошибка',w.error||'Нет'],['Время проверки',w.checkedAt]])info(d,k,v);if(w.changedFiles?.length)add(d,'pre',w.changedFiles.join('\n'),'list');add(d,'h3','Docker worktrees');for(const t of target.docker.worktrees){const panel=add(d,'section',undefined,'list');add(panel,'strong',(t.kind==='primary'?'PRIMARY':'WORKTREE')+' · '+(t.branch||'нет ветки'));for(const [k,v]of [['Путь',t.path],['HEAD',t.head],['Origin',t.origin],['Чистый',t.clean],['Изменено файлов',t.changed],['Target доступен',t.targetEligible],['Состояние',t.status],['GitHub SHA ветки',t.remoteSha],['GitHub relation',t.remoteRelation]])info(panel,k,v);if(t.changedFiles?.length)add(panel,'pre',t.changedFiles.join('\n'),'list');
prCiDetails(panel,t)}add(d,'h3','Действия');const actions=add(d,'div',undefined,'actions');for(const [name,action]of Object.entries(target.actions)){const a=add(actions,'div');add(a,'strong',name);const b=add(a,'button',action.available?'Выполнить':'Недоступно');b.disabled=true;add(a,'p',(action.reasons||[]).join(' · ')||'Нет доступного маршрута','muted')}if(ownerGitRepos.has(target.repoId)){
  add(d,'h3','Локальные Docker Git-действия (owner route)');
  add(d,'p','Только для явно разрешённых legacy репозиториев; действия над Docker primary недоступны.','muted');
  for(const t of target.docker.worktrees){
    if(t.kind!=='worktree'||!t.targetEligible||!t.task)continue;
    const box=add(d,'div',undefined,'actions');add(box,'strong',(t.branch||t.task)+' · '+t.task);
    for(const operation of ['stage','commit','push']){
      const b=add(box,'button',operation.toUpperCase());
      b.onclick=()=>startDockerAction(target.repoId,t.task,operation);
    }
  }
}
info(d,'Политика доступа','Windows Pull — только через Gateway ASK; Docker writes — ограниченный owner route.');}
async function load(remote){$('banner').textContent=remote?'Проверка GitHub (без fetch)…':'Чтение Docker и Windows…';$('local').disabled=$('remote').disabled=$('selected-remote').disabled=true;try{const response=await fetch('/api/v1/repositories'+(remote?'?remote=true':''),{cache:'no-store'});if(!response.ok)throw new Error((await response.json()).error||'HTTP '+response.status);const data=await response.json();rows=data.repositories;remoteChecked=remote;active=rows.some(x=>x.repoId===active)?active:rows[0]?.repoId||null;render();$('banner').textContent='Получено '+rows.length+' репозиториев · '+data.checkedAt+(remote?' · GitHub проверен':' · GitHub не проверен')+(data.errors.length?' · Ошибки: '+data.errors.join(', '):'')}catch(e){$('banner').textContent='Проверка не завершена: '+e.message+' · Последний снимок сохранён и может устареть';}finally{$('local').disabled=$('remote').disabled=$('selected-remote').disabled=false}}
async function checkAll(){
 if(checkController||!rows.length)return;
 checkController=new AbortController();const signal=checkController.signal;
 $('local').disabled=$('remote').disabled=$('selected-remote').disabled=true;$('cancel').disabled=false;
 const names=rows.map(r=>r.repoId);let next=0,done=0,failed=0;
 const worker=async()=>{
   while(next<names.length&&!signal.aborted){const id=names[next++];try{
      const response=await fetch('/api/v1/repositories/'+encodeURIComponent(id)+'?remote=true',{signal,cache:'no-store'});
      const body=await response.json();if(!response.ok)throw new Error(body.error||'GitHub unavailable');
      const index=rows.findIndex(r=>r.repoId===id);if(index>=0){rows[index]=body.repository;render()}
    }catch(e){if(!signal.aborted)failed++}finally{
      done++;$('banner').textContent='Проверено '+done+' из '+names.length+', ошибок запросов '+failed+'; PR/CI недоступность видна в деталях';
    }}
 };
 try{await Promise.all(Array.from({length:Math.min(4,names.length)},worker));
   $('banner').textContent=signal.aborted?'Проверка отменена; готовые результаты сохранены':
     'Проверено '+done+' из '+names.length+' · ошибок '+failed;
 }finally{checkController=null;$('local').disabled=$('remote').disabled=$('selected-remote').disabled=false;$('cancel').disabled=true}
}
async function checkSelected(){
 if(checkController||!active)return;
 const chosen=active;
 $('local').disabled=$('remote').disabled=$('selected-remote').disabled=true;
 $('banner').textContent='Проверяю GitHub, PR и CI для '+chosen+'…';
 try{
   const response=await fetch('/api/v1/repositories/'+encodeURIComponent(chosen)+'?remote=true',{cache:'no-store'});
   const data=await response.json();if(!response.ok)throw new Error(data.error||'Источник недоступен');
   const index=rows.findIndex(r=>r.repoId===chosen);
   if(index>=0){rows[index]=data.repository;render()}
   const errors=data.repository.github.readErrors||{};
   $('banner').textContent=chosen+': проверка завершена'+(Object.keys(errors).length?' · PR/CI: '+Object.entries(errors).map(([key,value])=>key+'='+value).join(', '):'');
 }catch(e){$('banner').textContent=chosen+': проверка не завершена — '+e.message}
 finally{$('local').disabled=$('remote').disabled=$('selected-remote').disabled=false}
}
$('cancel').onclick=()=>{if(checkController)checkController.abort()};
$('local').onclick=()=>load(false);$('remote').onclick=checkAll;$('selected-remote').onclick=checkSelected;initialize();

function dialogBox(title){const dlg=node('dialog');dlg.className='action-dialog';add(dlg,'h2',title);document.body.append(dlg);dlg.addEventListener('close',()=>dlg.remove());return dlg}
async function startDockerAction(repo,task,operation){
  try{
    $('banner').textContent='Получаю preview '+operation+' для '+repo+'/'+task+'…';
    const response=await fetch('/api/v1/docker-actions/preview/'+encodeURIComponent(operation)+'/'+encodeURIComponent(repo)+'/'+encodeURIComponent(task),{cache:'no-store'});
    const preview=await response.json();if(!response.ok)throw new Error(preview.error||'Preview failed');
    const dlg=dialogBox('Docker '+operation+' · '+repo+' / '+task);
    info(dlg,'Ветка',preview.branch);info(dlg,'HEAD',preview.head);
    info(dlg,operation==='push'?'Remote SHA':'Digest',preview.digest);
    const options={};
    if(operation==='stage'){
      const chosen=[];for(let i=0;i<preview.files.length;i++){
        const label=add(dlg,'label',undefined,'selectfile');const box=node('input');box.type='checkbox';box.value=i;
        label.append(box,document.createTextNode(' '+preview.files[i]));chosen.push(box);
      }
      options.indices=chosen;
    }else if(operation==='commit'){
      add(dlg,'p','Коммитит только уже staged файлы.','muted');
      for(const file of preview.files)add(dlg,'div',file,'muted');
      const input=node('textarea');input.placeholder='Сообщение коммита';input.maxLength=4000;dlg.append(input);options.message=input;
    }else add(dlg,'p','Обычный push без force. Только HEAD выбранной task-ветки.','warn');
    const controls=add(dlg,'div',undefined,'tools');
    const cancel=add(controls,'button','Отмена');cancel.onclick=()=>dlg.close();
    const confirm=add(controls,'button','Подтвердить '+operation);
    confirm.onclick=async()=>{
      const values=operation==='stage'?{indices:options.indices.filter(b=>b.checked).map(b=>Number(b.value))}:
        operation==='commit'?{message:options.message.value}:{};
      if(operation==='stage'&&!values.indices.length){alert('Выберите файлы');return}
      if(operation==='commit'&&!values.message.trim()){alert('Введите сообщение');return}
      if(!window.confirm('Выполнить '+operation+' для '+repo+'/'+task+' после проверки свежести preview?'))return;
      confirm.disabled=true;cancel.disabled=true;
      try{
        const result=await fetch('/api/v1/docker-actions/execute',{method:'POST',headers:{'Content-Type':'application/json',
          'X-Mirror-CSRF':csrf},body:JSON.stringify({previewId:preview.previewId,options:values})});
        const body=await result.json();if(!result.ok)throw new Error(body.error||'Action failed');
        dlg.close();$('banner').textContent='Выполнено: '+body.result;await load(false);
      }catch(e){$('banner').textContent='Результат требует проверки: '+e.message;dlg.close()}
    };
    dlg.showModal();$('banner').textContent='Preview готов для '+repo+'/'+task;
  }catch(e){$('banner').textContent='Действие недоступно: '+e.message}
}
async function initialize(){try{
 const [cap,session]=await Promise.all([fetch('/api/v1/capabilities'),fetch('/api/v1/csrf')]);
 if(cap.ok&&session.ok){const data=await cap.json(),auth=await session.json();
 ownerGitRepos=new Set(data.ownerGitRepos||[]);csrf=auth.csrf}
}catch(e){$('banner').textContent='API capabilities недоступны: '+e.message}await load(false)}
