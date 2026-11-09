/* Reference-design UI enhancement; all original auth and executor handlers remain. */
(() => {
  "use strict";
  const $=id=>document.getElementById(id);
  const grid=document.querySelector("#workspace-view .workspace-grid");
  if(!grid || !$("task-form") || !$("gate"))return;
  const work=grid.firstElementChild;
  const inspector=grid.querySelector(".inspector");
  const saved=work.querySelector(".saved");
  if(!inspector || !saved)return;
  inspector.classList.add("studio-context");
  work.classList.add("studio-work-column");
  grid.insertBefore(inspector,work);
  const right=document.createElement("aside");
  right.className="studio-right";
  right.setAttribute("aria-label","Pekerjaan, riwayat, dan pintasan");
  right.innerHTML='<section class="studio-widget" aria-label="Antrean saat ini"><div class="studio-widget-head"><h2>Pekerjaan Saat Ini</h2><a href="/jobs">Lihat antrean</a></div><div id="studio-current-jobs" aria-live="polite"><p class="studio-empty">Masuk untuk memuat pekerjaan tersimpan.</p></div></section>'+
  '<section class="studio-widget"><div class="studio-widget-head"><h2>Aksi Cepat</h2></div><div class="studio-quick">'+
  '<button id="studio-action-task" type="button"><span class="studio-quick-icon">＋</span>Tugas Baru</button>'+
  '<button id="studio-action-agent" type="button"><span class="studio-quick-icon">◇</span>Buat Agent</button>'+
  '<a href="/delegations"><span class="studio-quick-icon">⇄</span>Delegasikan Pekerjaan</a>'+
  '<a href="/automations"><span class="studio-quick-icon">ϟ</span>Buka Automations</a>'+
  '<button id="studio-action-note" type="button"><span class="studio-quick-icon">▤</span>Kelola Memori</button>'+
  '</div></section><section class="studio-widget" aria-label="Kesehatan kemampuan sistem">'+
  '<div class="studio-widget-head"><h2>Status Sistem</h2><button class="note-control" id="studio-refresh-status" type="button">Perbarui</button></div>'+
  '<div id="studio-system-status" aria-live="polite"><p class="studio-empty">Menunggu autentikasi pemilik.</p></div></section>';
  saved.classList.add("studio-saved");
  right.insertBefore(saved,right.children[1]);
  grid.append(right);

  const head=document.querySelector("#workspace-view .page-head");
  if(head?.querySelector("h1"))head.querySelector("h1").textContent="Agent Workspace";
  if(head?.querySelector(".lede"))head.querySelector(".lede").textContent="Berinteraksi, memberi tugas, dan mengelola pekerjaan AI Anda. Seluruh tindakan tetap mengikuti izin dan batas backend.";
  const identity=document.querySelector(".identity");
  if(identity){
    if(identity.querySelector(".logo"))identity.querySelector(".logo").textContent="⬡";
    if(identity.querySelector("strong"))identity.querySelector("strong").textContent="AI Automation Studio";
    if(identity.querySelector("small"))identity.querySelector("small").textContent="Personal AI Harness";
  }
  const stageTitle=document.querySelector("#workspace-view .stage-top strong");
  if(stageTitle)stageTitle.textContent="Ruang Percakapan & Tugas";
  const empty=$("empty");
  if(empty){
    empty.querySelector(".empty-symbol").textContent="✦";
    empty.querySelector("strong").textContent="Satu tempat untuk ide dan eksekusi.";
    empty.querySelector("p").textContent="Pilih Agent dan model yang tersedia. Jawaban serta hasil tugas berasal dari runtime yang dikonfigurasi, bukan demo visual.";
  }
  const contextHeader=document.createElement("div");
  contextHeader.innerHTML='<div class="studio-context-top"><span class="studio-robot" aria-hidden="true">🤖</span><div>'+
    '<div class="studio-agent-name" id="studio-agent-title">General Assistant</div>'+
    '<div class="studio-agent-subtitle" id="studio-agent-subtitle">Belum memilih Agent khusus</div></div></div>'+
    '<div class="studio-mini-label">Mode kerja</div><div class="studio-context-modes" role="group" aria-label="Mode kerja utama">'+
    '<button class="studio-mode" id="studio-mode-plan" type="button" data-selected="true">▤ Rencana</button>'+
    '<button class="studio-mode" id="studio-mode-ask" type="button" data-selected="false">◉ Chat Lokal</button>'+
    '<button class="studio-mode" id="studio-mode-cloud" type="button" data-selected="false">☁ Cloud AI</button></div>';
  inspector.insertBefore(contextHeader,inspector.firstChild.nextSibling);
  inspector.querySelector("h2").textContent="Agent Aktif";
  const agentSelect=$("agent-select");
  function syncAgent(){
    const selected=agentSelect.selectedOptions[0];
    $("studio-agent-title").textContent=selected?.value?(selected.textContent||selected.value).slice(0,74):"General Assistant";
    $("studio-agent-subtitle").textContent=selected?.value?"Profil tersimpan · izin backend berlaku":"Tanpa profil Agent khusus";
  }
  agentSelect.addEventListener("change",syncAgent);
  syncAgent();

  const modes=[["plan","mode-plan"],["ask","mode-ask"],["cloud","mode-cloud"]];
  function syncMode(){
    for(const [name,original] of modes){
      const selected=$(original).getAttribute("aria-pressed")==="true";
      $("studio-mode-"+name).dataset.selected=String(selected);
      $("studio-mode-"+name).setAttribute("aria-pressed",String(selected));
    }
  }
  for(const [name,original] of modes){
    $("studio-mode-"+name).addEventListener("click",()=>{$(original).click();syncMode();$("task").focus()});
    $(original).addEventListener("click",syncMode);
  }
  syncMode();

  const nav=document.querySelector(".rail .nav"),agentsNav=$("nav-agents");
  const toWorkspace=()=>$("nav-workspace").click();
  function extraNav(after,id,icon,label,fn){
    const node=document.createElement("button");
    node.type="button";node.id=id;node.setAttribute("title",label);node.setAttribute("aria-label",label);
    node.innerHTML='<b aria-hidden="true">'+icon+'</b><span>'+label+'</span>';
    after.insertAdjacentElement("afterend",node);node.addEventListener("click",fn);return node;
  }
  const memoryNav=extraNav(agentsNav,"studio-nav-memories","▤","Memories",()=>{
    toWorkspace();inspector.scrollIntoView({behavior:"smooth",block:"start"});agentSelect.focus();
  });
  extraNav(memoryNav,"studio-nav-projects","▱","Projects",()=>{
    toWorkspace();$("project").focus();inspector.scrollIntoView({behavior:"smooth",block:"start"});
  });
  const historyLink=nav.querySelector('a[href="/jobs"]');
  if(historyLink){
    historyLink.title="Jobs & History";historyLink.setAttribute("aria-label","Jobs & History");
    historyLink.querySelector("span").textContent="Jobs & History";
  }
  const advancedLink=nav.querySelector('a[href="/mission"]');
  if(advancedLink){
    advancedLink.title="Costs, limits & advanced controls";
    advancedLink.querySelector("span").textContent="Costs & Limits";
  }
  const backup=document.createElement("a");
  backup.href="https://github.com/naraya07pedro-spec/ai-automation-studio/blob/main/docs/ENCRYPTED_SCOPED_BACKUP_RECOVERY_V1.md";
  backup.target="_blank";backup.rel="noopener noreferrer";
  backup.innerHTML='<b aria-hidden="true">♧</b><span>Backup & Security</span>';
  backup.title="Panduan backup terenkripsi dan pemulihan, bukan hasil verifikasi live";
  backup.setAttribute("aria-label","Backup & Security documentation");
  nav.append(backup);
  const foot=document.querySelector(".rail-foot");
  if(foot)foot.innerHTML='<span class="studio-side-dot" aria-hidden="true"></span><strong>Private by design</strong><br>Setiap aksi mengikuti izin dan persetujuan runtime.';

  const toolbar=document.createElement("div");
  toolbar.className="studio-toolbar";
  toolbar.innerHTML='<div class="studio-search" id="studio-search-wrap" role="search">'+
    '<span aria-hidden="true">⌕</span><input id="studio-global-search" type="search" autocomplete="off" aria-label="Cari Agent" placeholder="Cari Agent di workspace…"><kbd>Ctrl K</kbd></div>'+
    '<a href="/jobs" class="studio-icon-link" title="Riwayat pekerjaan" aria-label="Riwayat pekerjaan">♧</a>'+
    '<span class="studio-avatar" aria-label="Owner workspace">AI</span>';
  const topbar=document.querySelector(".topbar");
  if(topbar)topbar.insertBefore(toolbar,topbar.querySelector(".session"));
  const search=$("studio-global-search"),searchWrap=$("studio-search-wrap");
  function activateSearch(){searchWrap.classList.add("studio-search-open");search.focus()}
  searchWrap.addEventListener("click",()=>{if(matchMedia("(max-width:620px)").matches)activateSearch()});
  search.addEventListener("blur",()=>{if(!search.value)searchWrap.classList.remove("studio-search-open")});
  search.addEventListener("keydown",event=>{
    if(event.key==="Escape"){search.value="";searchWrap.classList.remove("studio-search-open");search.blur()}
    if(event.key==="Enter"){
      event.preventDefault();agentsNav.click();
      const input=$("agent-search");
      if(input){input.value=search.value;input.dispatchEvent(new Event("input",{bubbles:true}));input.focus()}
    }
  });
  document.addEventListener("keydown",event=>{
    if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==="k"){event.preventDefault();activateSearch()}
  });

  $("studio-action-task").addEventListener("click",()=>{
    toWorkspace();$("task").focus();$("task").scrollIntoView({behavior:"smooth",block:"center"});
  });
  $("studio-action-agent").addEventListener("click",()=>{
    agentsNav.click();const create=$("new-agent");if(create&&!create.disabled)create.click();
  });
  $("studio-action-note").addEventListener("click",()=>{
    toWorkspace();inspector.scrollIntoView({behavior:"smooth",block:"start"});
    const note=$("new-note");if(note&&!note.disabled)note.click();else agentSelect.focus();
  });

  const modules=[
    {icon:"◇",title:"Agent Library",info:"Profil, peran, dan izin Agent yang benar-benar tersimpan.",action:"agents"},
    {icon:"⚙",title:"Konfigurasi Agent",info:"Atur instruksi Agent dan tool yang diizinkan.",action:"config"},
    {icon:"▤",title:"Memory & Knowledge",info:"Catatan per-Agent dan proyek dengan retensi.",action:"memory"},
    {icon:"⇄",title:"Delegations",info:"Serah-terima antar-Agent dengan persetujuan pemilik.",url:"/delegations"},
    {icon:"ϟ",title:"Automations",info:"Workflow yang tersimpan, bisa ditinjau, dan dijalankan.",url:"/automations"},
    {icon:"◷",title:"Jobs & History",info:"Antrean, bukti langkah, dan riwayat eksekusi.",url:"/jobs"},
    {icon:"▥",title:"Costs & Limits",info:"Provider dan batas permintaan di kontrol lanjutan.",url:"/mission"},
    {icon:"♧",title:"Backup & Security",info:"Panduan backup serta verifikasi pemulihan.",url:backup.href}
  ];
  const gallery=document.createElement("section");
  gallery.id="studio-feature-gallery";
  gallery.setAttribute("aria-label","Modul AI Automation Studio");
  gallery.innerHTML='<div class="studio-module-head"><div><h2>Kelola Seluruh AI Workspace</h2>'+
    '<p>Delapan modul terhubung ke halaman dan kontrol nyata.</p></div></div>'+
    '<div class="studio-modules" id="studio-module-grid"></div>'+
    '<div class="studio-info-banner"><span aria-hidden="true">ⓘ</span><span><strong>Status berasal dari runtime.</strong> Ilustrasi desain tidak menggantikan data asli. Tidak ada Agent fiktif, biaya karangan, atau backup yang diklaim berhasil tanpa pemeriksaan.</span></div>';
  for(const [index,item] of modules.entries()){
    const node=document.createElement(item.url?"a":"button");
    node.className="studio-module";
    if(item.url){
      node.href=item.url;
      if(item.title==="Backup & Security"){node.target="_blank";node.rel="noopener noreferrer"}
    }else node.type="button";
    const top=document.createElement("span");top.className="studio-module-top";
    const icon=document.createElement("span");icon.className="studio-module-icon";icon.textContent=item.icon;
    const indexLabel=document.createElement("span");indexLabel.className="studio-module-index";
    indexLabel.textContent=String(index+1).padStart(2,"0");
    top.append(icon,indexLabel);
    const heading=document.createElement("strong");heading.textContent=item.title;
    const description=document.createElement("small");description.textContent=item.info;
    node.append(top,heading,description);
    if(!item.url)node.addEventListener("click",()=>{
      if(item.action==="agents")agentsNav.click();
      if(item.action==="config"){agentsNav.click();$("agent-search").focus()}
      if(item.action==="memory"){toWorkspace();inspector.scrollIntoView({behavior:"smooth",block:"start"});agentSelect.focus()}
    });
    gallery.querySelector("#studio-module-grid").append(node);
  }
  grid.insertAdjacentElement("afterend",gallery);

  const jobsRoot=$("studio-current-jobs"),statusRoot=$("studio-system-status");
  function statusRow(label,value){
    const row=document.createElement("div");row.className="studio-check";
    const name=document.createElement("span");name.textContent=label;
    const info=document.createElement("strong");info.textContent=value;
    row.append(name,info);return row;
  }
  const noteText=t=>Object.assign(document.createElement("p"),{className:"studio-empty",textContent:t});
  async function jsonFrom(path){
    const r=await fetch(path,{credentials:"same-origin",cache:"no-store"});
    if(!r.ok)throw Error("http_"+r.status);
    return r.json();
  }
  let requestSeq=0;
  async function refreshLive(){
    const seq=++requestSeq;
    const owner=await jsonFrom("/api/operator-session").catch(()=>null);
    if(seq!==requestSeq)return;
    if(!owner?.authenticated){
      jobsRoot.replaceChildren(noteText("Buka sesi pemilik untuk melihat antrean."));
      statusRoot.replaceChildren(statusRow("Sesi pemilik","Terkunci"));return;
    }
    const [jobs,caps]=await Promise.allSettled([
      jsonFrom("/api/durable-jobs"),jsonFrom("/api/local-agent-capabilities")
    ]);
    if(seq!==requestSeq)return;
    jobsRoot.replaceChildren();
    if(jobs.status==="fulfilled"&&Array.isArray(jobs.value.jobs)){
      const list=jobs.value.jobs.slice(0,3);
      if(!list.length)jobsRoot.append(noteText("Belum ada pekerjaan dalam antrean."));
      for(const job of list){
        const card=document.createElement("div");card.className="studio-job-card";
        const name=document.createElement("strong");
        name.textContent=String(job.label||job.job_id||"Pekerjaan").slice(0,95);
        const detail=document.createElement("small");
        detail.textContent="Status: "+String(job.status||"Tidak diketahui")+" · "+String(job.job_id||"");
        card.append(name,detail);jobsRoot.append(card);
      }
    }else jobsRoot.append(noteText("Data antrean belum tersedia. Periksa halaman Jobs."));
    statusRoot.replaceChildren(statusRow("Sesi pemilik","Terautentikasi"));
    if(caps.status==="fulfilled"&&caps.value&&typeof caps.value==="object"){
      for(const [key,label] of [
        ["agent_profiles","Profil Agent"],
        ["durable_job_queue","Antrean pekerjaan"],
        ["typed_agent_handoff","Transfer antar-Agent"]
      ]){
        if(Object.prototype.hasOwnProperty.call(caps.value,key)&&typeof caps.value[key]==="boolean")
          statusRoot.append(statusRow(label,caps.value[key]?"Tersedia":"Tidak aktif"));
      }
      statusRoot.append(statusRow("Status backend","Respons diterima"));
    }else statusRoot.append(statusRow("Status backend","Belum dapat diperiksa"));
  }
  $("studio-refresh-status").addEventListener("click",refreshLive);
  const observer=new MutationObserver(()=>{refreshLive();syncAgent()});
  observer.observe($("gate"),{attributes:true,attributeFilter:["hidden"]});
  $("refresh-tasks")?.addEventListener("click",refreshLive);
  refreshLive();
})();