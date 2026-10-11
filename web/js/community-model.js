import {nameProblem} from './leaderboard-model.js';

export const REACTIONS = Object.freeze([
  {id:'love', emoji:'❤️', label:'Love'}, {id:'like', emoji:'👍', label:'Like'},
  {id:'fire', emoji:'🔥', label:'Fire'}, {id:'amazing', emoji:'⭐', label:'Amazing'},
  {id:'downvote', emoji:'👎', label:'Downvote'},
]);
export const RETENTION_NOTICE = "This round's recording will be deleted shortly after the prompt changes. Your grade stays saved.";
export function communityEnabled(config = globalThis.ARENA_CONFIG ?? {}) {
  return config.COMMUNITY_ENABLED === true && !config.DEMO_MODE && !config.LOCAL_MODE;
}
export function roundState(round, now = Date.now()) {
  if (!round) return 'none';
  const start = Date.parse(round.starts_at), end = Date.parse(round.ends_at);
  if (!round.id || !Number.isFinite(start) || !Number.isFinite(end) || start >= end) return 'closed';
  return now < start ? 'upcoming' : now >= end ? 'closed' : 'active';
}
export function closingLine(round, now = Date.now()) {
  const state = roundState(round, now);
  if (state === 'none') return 'No current community round. Check back later.';
  if (state === 'closed') return 'This round has closed. Check for the next prompt.';
  if (state === 'upcoming') return `Opens ${new Date(round.starts_at).toLocaleString()}.`;
  const seconds = Math.max(0, Math.ceil((Date.parse(round.ends_at) - now) / 1000));
  return `Closes ${new Date(round.ends_at).toLocaleString()} · ${Math.floor(seconds/3600)}h ${Math.floor(seconds%3600/60)}m ${seconds%60}s left`;
}
export function nextReaction(selected, clicked) {
  if (!REACTIONS.some(r => r.id === clicked)) throw new Error('Unknown reaction.');
  return selected === clicked ? null : clicked;
}
export function shareState(card, data, now = Date.now()) {
  if (!card.community_round_id) return {message:'Private practice. Record the Daily Prompt to upload a speech to the community board.'};
  if (roundState(data.round, now) !== 'active' || data.round.id !== card.community_round_id) return {message:'This community round has closed. Your grade stays saved.'};
  if (card.audio_deleted_at || (card.audio_expires_at && Date.parse(card.audio_expires_at) <= now)) return {message:'This recording has expired. Your grade stays saved.'};
  const entry = data.entry;
  if (entry && entry.speech_id !== card.id) return {message:'You already shared another speech in this round. You can only re-share that speech.'};
  if (entry && !entry.withdrawn_at) {
    const review = card.moderation_status === 'approved' ? 'Shared with the community.' : card.moderation_status === 'rejected' ? 'Review rejected this speech. It is hidden from the community.' : 'Submitted for review. It stays hidden until approved.';
    return {message:review, action:'withdraw', label:'Remove from the community board'};
  }
  if (card.moderation_status === 'rejected') return {message:'Review rejected this speech. It cannot appear on the community board.'};
  if (card.status !== 'done' || card.report?.gradable !== true || !card.audio_url) return {message:'Only completed, gradable speeches with an available recording can be uploaded.'};
  const explain = 'Uploading puts your score on the community board and lets signed-in listeners hear this recording and react until the round closes. Your report stays private.';
  if (entry) return {message:explain, action:'reshare', label:'Upload again'};
  // One step for a new speaker: the name they pick is saved, then the speech is uploaded.
  if (nameProblem(data.display_name)) return {message:`Pick the name the board will show. ${explain}`, action:'share', needsName:true, label:'Save name and upload'};
  return {message:explain, action:'share', label:'Upload to the community board'};
}

// Invalidate reads started before any mutation or round/tab change. Never allow
// a slow poll to erase the authoritative response to a later reaction request.
export function createBoardState() {
  let revision = 0;
  const state = {round:null, entries:[], sort:'grade', offset:0, pending:new Set(), hasMore:false};
  return Object.assign(state, {
    stamp:() => revision,
    invalidate:() => ++revision,
    accept(stamp, data) {
      if (stamp !== revision || state.pending.size) return false;
      state.round = data.round ?? null;
      state.entries = data.entries ?? [];
      state.hasMore = Boolean(data.has_more);
      return true;
    },
    async react(speechId, clicked, send) {
      const entry = state.entries.find(e => e.speech_id === speechId);
      if (!entry || entry.is_owner || state.pending.has(speechId) || roundState(state.round) !== 'active') return false;
      state.pending.add(speechId); ++revision;
      const roundId = state.round.id;
      try {
        const result = await send(nextReaction(entry.my_reaction, clicked));
        if (state.round?.id === roundId && state.entries.includes(entry)) {
          entry.counts = result.counts; entry.total_reactions = result.total_reactions; entry.reaction_score = result.reaction_score; entry.my_reaction = result.my_reaction;
        }
        return true;
      } finally { state.pending.delete(speechId); ++revision; }
    },
  });
}
