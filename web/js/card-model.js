// Pure card adapters. Keep quoted content as text; the view creates DOM text nodes.
const finite = v => typeof v === 'number' && Number.isFinite(v);
const list = v => Array.isArray(v) ? v : [];
const tokens = value => String(value ?? '').toLocaleLowerCase().match(/[\p{L}\p{N}]+/gu) ?? [];
const validTime = w => finite(w?.start) && finite(w?.end) && w.start >= 0 && w.end >= w.start;

export function metricRows(report) {
  return Object.entries(report?.factors ?? {}).flatMap(([factor, f]) => {
    const total = list(f.metrics).filter(m=>finite(m.score)).reduce((n, m) => n + (m.weight ?? 0), 0);
    const fraction = (f.weight_pct ?? 0) / 100;
    return [
      ...list(f.metrics).map(m => ({...m, factor, kind:'metric', overallLost: !finite(m.score) ? null : total ? fraction * (m.weight ?? 0) / total * (100 - m.score) : 0})),
      ...list(f.deductions).map(m => ({...m, factor, kind:'deduction', overallLost:fraction * (m.points_lost ?? 0)})),
    ];
  });
}

export function sectionScores(report, authoritative = {}) {
  const score = value => finite(value) ? value : finite(value?.score) ? value.score : null;
  const factors = report?.factors ?? {};
  const delivery = ['pace','diction','tone'].map(k=>factors[k]).filter(f=>finite(f?.score));
  const weight = delivery.reduce((n,f)=>n+(f.weight_pct ?? f.weight ?? 0),0);
  return {
    logos: score(authoritative?.logos) ?? factors.logic?.score ?? null,
    pathos: score(authoritative?.pathos) ?? factors.emotion?.score ?? null,
    delivery: score(authoritative?.delivery) ?? (weight ? delivery.reduce((n,f)=>n+f.score*(f.weight_pct ?? f.weight ?? 0),0)/weight : null),
  };
}

function phraseIndexes(phrase, words) {
  const needle = tokens(phrase);
  if (!needle.length) return [];
  // A transcription word can contain more than one lexical token.
  const stream = words.flatMap((w,i)=>tokens(w.text).map(t=>({t,i})));
  const matches=[];
  for(let i=0;i<=stream.length-needle.length;i++) {
    if(needle.every((t,j)=>stream[i+j].t===t)) matches.push([...new Set(stream.slice(i,i+needle.length).map(t=>t.i))]);
  }
  return matches;
}

export function quoteTime(quote, words = []) {
  for(const indexes of phraseIndexes(quote, list(words))) {
    if(indexes.every(i=>validTime(words[i]))) return words[indexes[0]].start;
  }
  return null;
}

export function transcriptModel(report) {
  const evidence = report?.evidence ?? {};
  const source = list(report?.words).filter(w=>w && typeof w.text === 'string');
  const fillers = new Set(list(evidence.diction?.fillers).flatMap(f=>phraseIndexes(typeof f==='string' ? f : f.text ?? f.quote,source).flat()));
  const overlaps = (w,r) => validTime(w) && finite(r?.start) && finite(r?.end) && r.start >= 0 && r.end > r.start && w.start < r.end && w.end > r.start;
  const words=source.map((w,i)=>({
    text:w.text, start:validTime(w)?w.start:null, end:validTime(w)?w.end:null,
    confidence:w.confidence, filler:fillers.has(i),
    monotone:list(evidence.tone?.monotone_ranges).some(r=>overlaps(w,r)),
    emphasis:list(evidence.tone?.emphasis_ranges).some(r=>overlaps(w,r)),
  }));
  const pauses=list(evidence.pace?.long_pauses).filter(p=>finite(p.at_seconds) && p.at_seconds>=0 && finite(p.seconds) && p.seconds>0)
    .map(p=>({start:p.at_seconds, seconds:p.seconds})).sort((a,b)=>a.start-b.start);
  const text=String(report?.transcript ?? source.map(w=>w.text).join(' '));
  // Align timings to original lexical spans. Untimed text, spacing and punctuation
  // remain intact even if Scribe omitted words or the grader rejected bad timings.
  const lexemes=[...text.matchAll(/[\p{L}\p{N}]+/gu)].map(m=>({text:m[0].toLocaleLowerCase(),start:m.index,end:m.index+m[0].length}));
  const segments=[];
  let lexicalCursor=0, textCursor=0;
  words.forEach((word,index)=>{
    const needle=tokens(word.text);
    if(!needle.length) return;
    for(let i=lexicalCursor;i<=lexemes.length-needle.length;i++) {
      if(!needle.every((t,j)=>lexemes[i+j].text===t)) continue;
      const start=lexemes[i].start, end=lexemes[i+needle.length-1].end;
      if(start>textCursor) segments.push({text:text.slice(textCursor,start),word:null});
      segments.push({text:text.slice(start,end),word:index});
      lexicalCursor=i+needle.length;textCursor=end;return;
    }
  });
  if(textCursor<text.length) segments.push({text:text.slice(textCursor),word:null});
  return {text, words, pauses, segments, timed:segments.some(s=>s.word!==null && words[s.word].start!==null)};
}

const found = (label,value) => ({text:`${label}: ${value ? 'found' : 'not found'}`, quote:false});
const quoted = (value, label='') => typeof value==='string' ? {text:value,label,quote:true} : {
  text:String(value?.quote ?? value?.text ?? value?.claim ?? ''),
  label:label || value?.type || '', quote:true,
  start:finite(value?.start) ? value.start : finite(value?.at_seconds) ? value.at_seconds : finite(value?.ends_at_seconds) ? value.ends_at_seconds : null,
};

export function stanceEvidence(report) {
  const verification=report?.evidence?.argument?.stance_verification;
  const seen=new Set();
  return [['Speech position',verification?.primary],['Confirmation',verification?.confirmation]]
    .filter(([,check])=>{
      if(check?.quote_verified!==true || typeof check.evidence_quote!=='string' || !check.evidence_quote.trim() || seen.has(check.evidence_quote)) return false;
      seen.add(check.evidence_quote);return true;
    }).map(([label,check])=>quoted(check.evidence_quote,label));
}

export function evidenceForMetric(report, metric) {
  const e=report?.evidence ?? {}, a=e.argument ?? {}, p=e.pace ?? {}, d=e.diction ?? {}, t=e.tone ?? {}, w=e.word_lists ?? {};
  const claims=()=>list(a.arguments).map(c=>({text:String(c.claim ?? ''),label:`${c.status ?? 'unknown'} · ${c.support_type ?? 'none'}`,quote:true}));
  switch(metric) {
    case 'supported_arguments': case 'unsupported_claim_count': return claims();
    case 'structure_points': return [found('Thesis',a.thesis_found),found('Conclusion',a.conclusion_found)];
    case 'counterargument_level': return [found('Counterargument',a.counterargument_found),found('Rebuttal',a.rebuttal_found)];
    case 'stance_mismatch': case 'stance_unclear': return [
      {text:`Chosen: ${a.side_chosen ?? report?.inputs?.side ?? 'unknown'}; argued: ${a.stance_argued ?? 'unknown'}`,quote:false},
      ...(a.stance_verification?.message ? [{text:a.stance_verification.message,quote:false}] : []),
      ...stanceEvidence(report),
    ];
    case 'reasoning_fallacy_count': return list(a.fallacies).map(v=>quoted(v));
    case 'emotional_appeals': case 'manipulative_appeal_count': return list(a.emotional_appeals).map(v=>quoted(v));
    case 'fillers_per_100_words': return list(d.fillers).map(v=>quoted(v,'Filler'));
    case 'repeated_word_count': return list(d.repeated_words).map(v=>quoted(v,'Repeated'));
    case 'connectives_per_100_words': return list(w.connectives).map(v=>quoted(v,'Connective'));
    case 'audience_address_per_100_words': return list(w.audience_words).map(v=>quoted(v,'Audience'));
    case 'emotion_words_per_100_words': return list(w.emotion_words).map(v=>quoted(v,'Emotion'));
    case 'hedges_per_100_words': return list(t.hedges).map(v=>quoted(v,'Hedge'));
    case 'uptalk_pct': return list(t.uptalk_sentences).map(v=>quoted(v,'Rising pitch'));
    case 'long_pause_count': case 'pause_time_pct': return list(p.long_pauses).map(v=>({text:`${v.seconds} s pause`,start:v.at_seconds,quote:false}));
    case 'monotone_window_count': return list(t.monotone_ranges).map(v=>({text:`Monotone: ${v.start}–${v.end} s`,start:v.start,quote:false}));
    case 'pitch_variation_pct': return list(t.window_pitch_variation_pct).map((v,i)=>({text:`Window ${i+1}: ${v}% pitch variation`,quote:false}));
    case 'words_per_minute': return list(p.window_wpm).map((v,i)=>({text:`Window ${i+1}: ${v} wpm`,quote:false}));
    default: return [];
  }
}

export function retryHref(card) {
  const params = new URLSearchParams();
  if(card?.motion_id) params.set('motion_id',card.motion_id);
  if(card?.side) params.set('side',card.side);
  return `practice.html${params.size ? '?'+params : ''}`;
}

export function safeAudioUrl(value, base) {
  if(typeof value!=='string' || !value.trim()) return null;
  const trimmed=value.trim();
  if(trimmed.startsWith('//') || trimmed.includes('\\') || !/^(https?:\/\/|\/)/i.test(trimmed)) return null;
  try { const url=new URL(trimmed,base); return ['https:','http:'].includes(url.protocol)?url.href:null; } catch { return null; }
}
