import {communityEnabled, REACTIONS, roundState, closingLine, createBoardState} from './community-model.js';
import {safeAudioUrl} from './card-model.js';

export function mountCommunityBoard({api, root=document.getElementById('community'), now=Date.now} = {}) {
  if (!root || !communityEnabled()) return;
  root.hidden = false;
  const $ = id => root.querySelector(`#${id}`);
  const state = createBoardState(), rows = new Map();
  let alive = true, loading = false, timer, clock, roundId, requestId=0, closedRound;
  const el = (tag, text) => {const e=document.createElement(tag); if(text!==undefined) e.textContent=text; return e;};
  function clear() {
    for (const row of rows.values()) { row.audio.pause(); row.audio.removeAttribute('src'); row.audio.load(); }
    rows.clear(); $('community-entries').replaceChildren();
  }
  function counts(row, entry) {
    row.name.textContent = `#${entry.rank} · ${entry.display_name}${entry.is_owner ? ' (you)' : ''}`;
    const score = entry.reaction_score ?? ((entry.total_reactions ?? 0) - 2 * (entry.counts?.downvote ?? 0));
    row.detail.textContent = `${String(entry.side).toUpperCase()} · ${entry.overall} / 100${entry.letter ? ` · ${entry.letter}` : ''} · ${entry.total_reactions ?? 0} reactions · Community score: ${score}`;
    const active = roundState(state.round,now()) === 'active';
    for (const [id, button] of row.buttons) {
      const reaction = REACTIONS.find(r=>r.id===id), count = entry.counts?.[id] ?? 0;
      button.textContent = `${reaction.emoji} ${reaction.label} ${count}`;
      button.setAttribute('aria-label',`${reaction.label}: ${count} reactions${entry.is_owner ? '. You cannot react to your own speech' : ''}`);
      button.setAttribute('aria-pressed',String(entry.my_reaction===id));
      button.disabled = !active || entry.is_owner || state.pending.has(entry.speech_id);
    }
    row.play.disabled = !active || row.signing;
  }
  function createRow(entry) {
    const li=el('li'); li.className='community-entry'; li.dataset.speechId=entry.speech_id;
    const row={li,name:el('h3'),detail:el('p'),play:el('button','Load recording'),audio:el('audio'),message:el('p'),buttons:new Map(),signing:false};
    row.detail.className=row.message.className='community-copy'; row.message.setAttribute('role','status');
    row.play.type='button'; row.audio.controls=true; row.audio.preload='none'; row.audio.hidden=true;
    row.audio.setAttribute('aria-label',`Recording by ${entry.display_name}`);
    const reactions=el('div'); reactions.className='reactions'; reactions.setAttribute('role','group'); reactions.setAttribute('aria-label','React to this speech');
    for (const reaction of REACTIONS) {
      const button=el('button'); button.type='button'; row.buttons.set(reaction.id,button); reactions.append(button);
      button.addEventListener('click',async()=>{
        const current=state.entries.find(e=>e.speech_id===entry.speech_id);
        if (!current) return;
        row.message.textContent='Saving reaction…';
        const promise=state.react(current.speech_id,reaction.id,value=>api(`/api/community/speeches/${encodeURIComponent(current.speech_id)}/reaction`,{method:'POST',body:{reaction:value}}));
        counts(row,current); navigation();
        try { await promise; if(alive && rows.get(current.speech_id)===row) row.message.textContent='Reaction saved.'; }
        catch(error) { if(alive && rows.get(current.speech_id)===row) row.message.textContent=`${error.message} Showing your previous reaction. Retry or refresh to confirm.`; }
        finally { if(alive && rows.get(current.speech_id)===row) counts(row,current); if(alive)navigation(); }
        // Refresh ranking after success or failure (lost responses may have committed).
        if(alive) load();
      });
    }
    row.play.addEventListener('click',async()=>{
      if(row.signing || roundState(state.round,now())!=='active') return;
      const requestedRound=state.round.id;
      row.signing=true; row.play.disabled=true; row.message.textContent='Getting playback link…';
      try {
        const data=await api(`/api/community/speeches/${encodeURIComponent(entry.speech_id)}/audio`);
        if(!alive || state.round?.id!==requestedRound || rows.get(entry.speech_id)!==row || roundState(state.round,now())!=='active') return;
        const url=safeAudioUrl(data.audio_url,globalThis.ARENA_CONFIG?.API_URL || location.href);
        if(!url) throw new Error('No valid recording link was returned.');
        for(const other of rows.values()) if(other!==row) other.audio.pause();
        row.audio.src=url; row.audio.hidden=false; row.audio.load();
        row.message.textContent='Recording ready. Use the audio controls to play.';
        row.play.textContent='Refresh recording link';
      } catch(error) { if(alive && rows.get(entry.speech_id)===row) row.message.textContent=`${error.message} Try loading the recording again.`; }
      finally { row.signing=false; if(alive && rows.get(entry.speech_id)===row) counts(row,state.entries.find(e=>e.speech_id===entry.speech_id) ?? entry); }
    });
    row.audio.addEventListener('error',()=>{row.message.textContent='Playback link may have expired. Load the recording again for a fresh link.';});
    row.audio.addEventListener('play',()=>{for(const other of rows.values()) if(other!==row) other.audio.pause();});
    li.append(row.name,row.detail,row.play,row.audio,reactions,row.message);
    return row;
  }
  function draw() {
    const current=state.round?.id ?? null;
    if(current!==roundId) { clear(); roundId=current; }
    $('community-prompt').textContent=state.round?.prompt?.text ?? '';
    $('community-closing').textContent=closingLine(state.round,now());
    const status=roundState(state.round,now());
    if(status!=='active') { clear(); if(status==='closed')closedRound=current; $('community-status').textContent=closingLine(state.round,now()); }
    else {
      $('community-status').textContent=state.entries.length ? 'One reaction per listener. Positive reactions add 1; downvotes subtract 1 from Community favorites. Select your reaction again to remove it.' : 'No shared speeches yet. Record this prompt to be the first.';
      const ids=new Set(state.entries.map(e=>e.speech_id));
      for(const [id,row] of rows) if(!ids.has(id)) {row.audio.pause();row.audio.removeAttribute('src');row.audio.load();row.li.remove();rows.delete(id);}
      state.entries.forEach((entry,index)=>{
        let row=rows.get(entry.speech_id); if(!row) {row=createRow(entry);rows.set(entry.speech_id,row);}
        counts(row,entry);
        // No replacement or detach for a row that stays in the same position.
        const list=$('community-entries'), before=list.children[index] ?? null;
        if(before!==row.li) {
          if(row.li.isConnected && list.moveBefore) list.moveBefore(row.li,before);
          else {
            const playing=!row.audio.paused, time=row.audio.currentTime;
            list.insertBefore(row.li,before);
            // Older engines do not have state-preserving DOM moves.
            if(playing) {row.audio.currentTime=time;row.audio.play().catch(()=>{row.message.textContent='Use the audio controls to resume playback.';});}
          }
        }
      });
    }
    navigation();
  }
  function navigation() {
    const pending=state.pending.size>0, active=roundState(state.round,now())==='active';
    $('community-prev').disabled=state.offset===0 || pending || !active;
    $('community-next').disabled=!state.hasMore || pending || !active;
    for(const tab of root.querySelectorAll('[role=tab]'))tab.disabled=pending;
  }
  async function load() {
    if(!alive || document.hidden || state.pending.size) return;
    const stamp=state.stamp(), request=++requestId; loading=true;
    try {
      // Always ask for current round; pin only page reads after confirming it.
      const current=await api('/api/community/round');
      if(!alive || request!==requestId || stamp!==state.stamp()) return;
      if(current.round?.id!==state.round?.id) {state.offset=0; state.accept(stamp,{round:current.round,entries:[]}); draw();}
      if(roundState(current.round,now())!=='active') {state.accept(stamp,{round:current.round,entries:[]});draw();$('community-error').textContent='';return;}
      const query=new URLSearchParams({sort:state.sort,limit:'20',offset:String(state.offset),round_id:current.round.id});
      const data=await api(`/api/community/leaderboard?${query}`);
      if(!alive || request!==requestId || data.round?.id!==current.round.id) return;
      if(state.accept(stamp,data)) { draw(); $('community-error').textContent=''; }
    } catch(error) { if(alive && request===requestId && stamp===state.stamp()) $('community-error').textContent=`${error.message} Could not refresh community speeches. Retry.`; }
    finally { if(request===requestId)loading=false; }
  }
  function resetView() {state.invalidate();state.entries=[];clear();draw();load();}
  const tabs=[...root.querySelectorAll('[role=tab]')];
  function selectTab(tab) {
    if(state.pending.size) return;
    state.sort=tab.dataset.sort;state.offset=0;
    for(const other of tabs) {other.setAttribute('aria-selected',String(other===tab));other.tabIndex=other===tab?0:-1;}
    $('community-results').setAttribute('aria-labelledby',tab.id);
    resetView();
  }
  tabs.forEach((tab,index)=>{
    tab.addEventListener('click',()=>selectTab(tab));
    tab.addEventListener('keydown',event=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(event.key)){event.preventDefault();const next=event.key==='Home'?0:event.key==='End'?tabs.length-1:(index+(event.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;selectTab(tabs[next]);tabs[next].focus();}});
  });
  $('community-prev').addEventListener('click',()=>{state.offset=Math.max(0,state.offset-20);resetView();});
  $('community-next').addEventListener('click',()=>{state.offset+=20;resetView();});
  $('community-retry').addEventListener('click',()=>{if(!loading)load();});
  function schedule() {clearInterval(timer);if(!document.hidden)timer=setInterval(()=>{if(!loading)load();},30000);}
  document.addEventListener('visibilitychange',()=>{schedule();if(!document.hidden)load();});
  clock=setInterval(()=>{
    if(document.hidden || !alive) return;
    $('community-closing').textContent=closingLine(state.round,now());
    if(roundState(state.round,now())==='closed' && state.round.id!==closedRound) {state.invalidate();state.entries=[];clear();draw();load();}
  },1000);
  window.addEventListener('pagehide',()=>{alive=false;clearInterval(timer);clearInterval(clock);state.invalidate();clear();});
  schedule();load();
  return {state,load};
}
