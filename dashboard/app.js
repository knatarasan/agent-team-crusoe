/* Recorded run explorer. No model calls, keys, or external dependencies. */
(() => {
  'use strict';
  let data = window.DEMO_DATA;
  let selected = 0, visibleCount = Infinity, timer = null;
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const pct = value => `${(Number(value) * 100).toFixed(1)}%`;
  const statusClass = value => value === 'rolled back' ? 'rolled-back' : value === 'kept' ? 'kept' : 'baseline';
  const runs = () => data.runs.slice(0, visibleCount);
  function bestOf(list) { return [...list].reverse().find(r => r.status !== 'rolled back') || list[0]; }
  function switchTab(name) {
    document.querySelectorAll('.view').forEach(el => el.hidden = el.id !== name);
    document.querySelectorAll('.nav').forEach(el => el.classList.toggle('active', el.dataset.tab === name));
    location.hash = name;
  }
  document.querySelectorAll('[data-tab]').forEach(el => el.onclick = () => switchTab(el.dataset.tab));
  document.querySelectorAll('[data-go]').forEach(el => el.onclick = () => switchTab(el.dataset.go));
  function metrics(list) {
    const first = list[0], best = bestOf(list);
    const gain = (best.score - first.score) * 100;
    const cards = [
      ['Retained best score', pct(best.score), `${gain >= 0 ? '+' : ''}${gain.toFixed(1)} pp`, `Baseline ${pct(first.score)} · cost-weighted`],
      ['Exact match', pct(best.exact), `${((best.exact - first.exact)*100).toFixed(0)} pp gain`, `Baseline ${pct(first.exact)} · all decision fields`],
      ['Wrongful denials', String(best.denials), `${first.denials - best.denials} fewer`, `Baseline ${first.denials} · retained config`],
      ['Best playbook', `v${best.version}`, `${(data.configs[String(best.version)]?.rules || []).filter(r => r.status === 'active').length} rules`, `Retained from run ${best.n} · ${data.claim_count} claims per run`]
    ];
    $('metrics').innerHTML = cards.map(c=>`<article class="metric"><div class="metric-label">${c[0]}</div><div class="metric-value">${c[1]}<small>${c[2]}</small></div><div class="metric-sub">${esc(c[3])}</div></article>`).join('');
  }
  function chart(list) {
    const w=680,h=270,l=45,r=28,t=24,b=38,low=0.4;
    const x=i=>list.length===1?(w+l-r)/2:l+i*(w-l-r)/(list.length-1);
    const y=v=>t+(1-v)/(1-low)*(h-t-b);
    let svg=`<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="Recorded cost-weighted score, exact match, and retained best across runs">`;
    for(let v=.4;v<1.01;v+=.2) svg+=`<line x1="${l}" x2="${w-r}" y1="${y(v)}" y2="${y(v)}" stroke="#e9eef0"/><text x="${l-11}" y="${y(v)+4}" text-anchor="end" fill="#87979e" font-size="10">${Math.round(v*100)}%</text>`;
    const best=[];let current=list[0].score;
    list.forEach(run=>{if(run.status!=='rolled back') current=run.score;best.push(current);});
    const points=values=>values.map((v,i)=>`${x(i)},${y(v)}`).join(' ');
    const step=[];best.forEach((v,i)=>{if(i)step.push(`${x(i)},${y(best[i-1])}`);step.push(`${x(i)},${y(v)}`);});
    svg+=`<polyline points="${step.join(' ')}" fill="none" stroke="#a6b4b7" stroke-width="2" stroke-dasharray="5 5"/><polyline points="${points(list.map(a=>a.exact))}" fill="none" stroke="#6c93c5" stroke-width="2"/><polyline points="${points(list.map(a=>a.score))}" fill="none" stroke="#148066" stroke-width="2.8"/>`;
    list.forEach((run,i)=>{svg+=`<text x="${x(i)}" y="${h-10}" text-anchor="middle" fill="#809098" font-size="10">Run ${run.n} · v${run.version}</text><circle cx="${x(i)}" cy="${y(run.exact)}" r="3" fill="#6c93c5"/><circle cx="${x(i)}" cy="${y(run.score)}" r="${i===selected?6:4}" fill="white" stroke="${run.status==='rolled back'?'#bd7468':'#148066'}" stroke-width="2.5"><title>Run ${run.n}: ${pct(run.score)}, ${esc(run.status)}</title></circle><text x="${x(i)}" y="${y(run.score)-14}" text-anchor="middle" fill="#3b665b" font-size="11" font-weight="600">${pct(run.score)}</text>`;});
    $('chart').innerHTML=svg+'</svg>';
  }
  function detail(list) {
    const run=list[selected];const prior=bestOf(list.slice(0,selected));
    const diff=prior?(run.score-prior.score)*100:0;
    let reason='The initial team establishes the reference score. No learned house rules have been added yet.';
    if(prior) {
      if(run.status==='kept') reason=`Kept: score rose ${diff.toFixed(1)} percentage points over the previous best, and wrongful denials ${run.denials<=prior.denials?'did not increase':'increased'}.`;
      else if(run.denials>prior.denials) reason=`Rolled back: wrongful denials increased from ${prior.denials} to ${run.denials}. The previous best v${prior.version} stays in use.`;
      else reason=`Rolled back: score changed ${diff>=0?'+':''}${diff.toFixed(1)} percentage points vs. the previous best, failing the improvement gate. v${prior.version} stays in use.`;
    }
    const changes=run.edits?.length?run.edits.map(e=>e.edit):String(run.changes||'').split(' ; ').filter(x=>x&&x!=='—');
    $('selected-run').innerHTML=`<div class="selected-title"><h2>Run ${run.n} <span style="color:#95a4a9;font-weight:400">/</span> Config v${run.version}</h2><span class="verdict-pill ${statusClass(run.status)}">${esc(run.status)}</span><button class="text-button" id="inspect-config" style="margin-left:auto;margin-top:0">Inspect this playbook →</button></div><p class="gate">${esc(reason)}</p><div class="edit-list">${changes.length?changes.map((e,i)=>`<div class="edit"><div class="eyebrow">TESTED EDIT ${i+1}</div>${esc(e)}</div>`).join(''):'<div class="empty">Baseline prompts only. The supervisor proposes the next configuration after reviewing this run.</div>'}</div>`;
    if(run.recovery) $('selected-run').innerHTML+='<p class="footnote" style="margin-top:14px">Scored decision fields were recovered from the saved evaluation trace after an export error. Original narrative rationales are unavailable. No claim inference was repeated.</p>';
    else if(run.restored_summary_only) $('selected-run').innerHTML+='<p class="footnote" style="margin-top:14px">Historical summary recovered from the archived report. Original per-claim prediction traces are unavailable.</p>';
    $('inspect-config').onclick=()=>{$('version-select').value=String(run.version);renderRules();switchTab('playbook');};
  }
  function renderRuns() {
    const list=runs();if(!list.length){$('metrics').innerHTML='<div class="empty">No completed runs yet. Run triage run to create evidence.</div>';return;}
    selected=Math.min(selected,list.length-1);metrics(list);chart(list);detail(list);
    $('run-count').textContent=`${list.length} completed runs`;
    $('run-list').innerHTML=list.map((run,i)=>`<button class="run-button ${i===selected?'selected':''}" data-run="${i}" aria-pressed="${i===selected}"><span class="run-number">${String(run.n).padStart(2,'0')}</span><span class="run-info"><b>Config v${run.version}</b><small>${run.denials} wrongful denials</small></span><span class="run-score">${pct(run.score)}<span class="verdict ${statusClass(run.status)}">${esc(run.status)}</span></span></button>`).join('');
    document.querySelectorAll('[data-run]').forEach(el=>el.onclick=()=>{selected=Number(el.dataset.run);renderRuns();});
  }
  function renderRules() {
    const version=$('version-select').value;const config=data.configs[version];
    const run=data.runs.find(r=>String(r.version)===version);const best=bestOf(data.runs);
    $('config-status').textContent=Number(version)===best?.version?'Retained best · these rules survived evaluation.':run?`Tested in run ${run.n} · ${run.status}.`:'Proposed configuration · not yet evaluated in this run history.';
    $('rules').innerHTML=(config?.rules||[]).map(rule=>`<article class="panel"><div class="rule-meta"><span class="rule-id">${esc(rule.id)}</span><span class="tag">${esc(rule.role)} / ${esc(rule.state)}</span></div><p class="rule-text">${esc(rule.text)}</p><div class="source-links">Learning sources ${ (rule.source_claim_ids||[]).map(id=>`<button class="claim-link" data-claim="${esc(id)}">#${esc(id)}</button>`).join('')} <span style="float:right">${esc(rule.status)}</span></div></article>`).join('')||'<div class="empty">v0 starts with fixed base prompts and an empty playbook.</div>';
    $('instructions').innerHTML=Object.entries(config?.role_instructions||{}).map(([k,v])=>`<p><b>${esc(k)}</b> · ${esc(v)}</p>`).join('')||'<p class="footnote">No role prompt add-ons in this configuration.</p>';
    $('source-claim').hidden=true;
    document.querySelectorAll('[data-claim]').forEach(el=>el.onclick=()=>{const claim=data.claims.find(c=>String(c.id)===el.dataset.claim);const box=$('source-claim');box.hidden=false;box.innerHTML=claim?`<div class="eyebrow">RULE SOURCE / CLAIM #${esc(claim.id)} · ${esc(claim.state)}</div><h2>A mistake becomes a general rule</h2><p>${esc(claim.description)}</p><b>Expected outcome in the dataset</b><p>${esc(claim.expected)}</p><p class="footnote">These source claims informed the supervisor. Per-claim before/after predictions are not included in the archived report.</p>`:'<p>Source claim details are unavailable in this snapshot.</p>';box.scrollIntoView({behavior:'smooth',block:'center'});});
  }
  function evidence() {
    const list=data.runs;if(!list.length)return;const first=list[0],best=bestOf(list);
    $('comparison').innerHTML=`<table><thead><tr><th>Metric</th><th>Baseline v${first.version}</th><th>Best v${best.version}</th></tr></thead><tbody><tr><td>Cost-weighted score</td><td>${pct(first.score)}</td><td class="positive">${pct(best.score)}</td></tr><tr><td>Exact match</td><td>${pct(first.exact)}</td><td class="positive">${pct(best.exact)}</td></tr><tr><td>Wrongful denials</td><td>${first.denials}</td><td class="positive">${best.denials}</td></tr><tr><td>Working-set claims</td><td>${data.claim_count}</td><td>${data.claim_count}</td></tr><tr><td>Unseen claims scored</td><td>Not evaluated</td><td>Not evaluated</td></tr></tbody></table>`;
    const cats=Object.keys(first.categories||{});
    $('categories').innerHTML=cats.length?`<table><thead><tr><th>Gold route</th><th>Baseline</th><th>Retained best</th><th>Change</th></tr></thead><tbody>${cats.map(cat=>{const a=first.categories[cat],b=best.categories[cat];if(b==null)return '';const gain=(b-a)*100;return `<tr><td>${esc(cat.replaceAll('_',' '))}</td><td>${pct(a)}</td><td>${pct(b)}</td><td class="${gain>=0?'positive':'negative'}">${gain>=0?'+':''}${gain.toFixed(1)} pp</td></tr>`;}).join('')}</tbody></table>`:'<p class="footnote">Category breakdown unavailable.</p>';
    $('evidence-source').textContent=`${data.origin}. ${data.recorded?'Recovered from the repository’s saved history report; this is a recording, not a fresh inference run.':'Read from runs/history.csv, summary.json and state.json. Refresh after a completed run.'} Reported scores are rounded.`;
  }
  function init() {
    if(!data||!Array.isArray(data.runs)){$('provenance').textContent='No snapshot loaded. Start with: uv run triage dashboard';return;}
    $('source-tag').textContent=data.recorded?'Archived run recording':'Saved local runs';
    $('provenance').textContent=`${data.origin} · ${data.claim_count} claims per run`;
    $('footer-source').textContent=data.recorded?'RECORDED EVIDENCE / 1455bf4':'LOCAL RUN ARTIFACTS';
    const best=bestOf(data.runs);
    $('version-select').innerHTML=Object.keys(data.configs).sort((a,b)=>Number(a)-Number(b)).map(v=>`<option value="${esc(v)}">v${esc(v)}${Number(v)===best?.version?' · retained best':''}</option>`).join('');
    if(best)$('version-select').value=String(best.version);
    $('version-select').onchange=renderRules;
    selected=Math.max(0,data.runs.length-1);renderRuns();renderRules();evidence();
  }
  function stopReplay() {clearInterval(timer);timer=null;$('replay').textContent='▶ Replay experiment';}
  $('replay').onclick=()=>{
    if(timer){stopReplay();$('replay-status').textContent='Replay paused · select any run to inspect it';return;}
    visibleCount=1;selected=0;switchTab('overview');renderRuns();$('replay').textContent='Ⅱ Pause replay';$('replay-status').textContent='Replaying recorded run 1 · no model calls';
    timer=setInterval(()=>{visibleCount++;selected=visibleCount-1;renderRuns();$('replay-status').textContent=`Replaying recorded run ${Math.min(visibleCount,data.runs.length)} · no model calls`;if(visibleCount>=data.runs.length){stopReplay();visibleCount=Infinity;$('replay-status').textContent=`Replay complete · ${data.runs.filter(r=>r.status==='kept').length} candidate configurations retained · inspect the log for details`; }},3000);
    if(data.runs.length<=1)stopReplay();
  };
  $('refresh').onclick=async()=>{
    const button=$('refresh');button.disabled=true;
    try{const res=await fetch(`/api/data?t=${Date.now()}`,{cache:'no-store'});if(!res.ok)throw Error();const next=await res.json();if(!Array.isArray(next.runs))throw Error();stopReplay();data=next;visibleCount=Infinity;init();$('replay-status').textContent='Snapshot refreshed from local run artifacts';}
    catch{$('replay-status').textContent='This is an exported snapshot. To refresh after new runs, use: uv run triage dashboard';}
    finally{button.disabled=false;}
  };
  $('download').onclick=()=>{const blob=new Blob([JSON.stringify(data,null,2)],{type:'application/json'});const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download='version-n-plus-one-evidence.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
  init();if(['overview','playbook','evidence'].includes(location.hash.slice(1)))switchTab(location.hash.slice(1));
})();
