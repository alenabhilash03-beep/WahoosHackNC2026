-- Community rounds, opt-in shared speeches and emoji reactions; apply after 0005.
-- Additive: existing speeches stay untagged, private and unchanged.
-- Like every other table, these are reached only by the FastAPI server with the
-- service-role key: RLS is on with no policies, and the functions below are not
-- callable with the public or account keys.

-- One community prompt at a time. The window, not a scheduled job, decides what is open.
create table public.community_rounds (
  id          uuid primary key default gen_random_uuid(),
  motion_id   uuid not null references public.motions (id),
  starts_at   timestamptz not null,
  ends_at     timestamptz not null,
  created_at  timestamptz not null default now(),
  constraint community_rounds_window_check check (starts_at < ends_at),
  -- Two rounds can never be open at once, even if they are created at the same moment.
  constraint community_rounds_no_overlap
    exclude using gist (tstzrange(starts_at, ends_at, '[)') with &&)
);

-- A speech recorded for a round is tagged with it and its recording gets an expiry.
-- Cleanup clears audio_path after deleting the file, so the column becomes nullable.
alter table public.speeches alter column audio_path drop not null;
alter table public.speeches add column community_round_id uuid references public.community_rounds (id);
alter table public.speeches add column audio_expires_at timestamptz;
alter table public.speeches add column audio_deleted_at timestamptz;
-- Lets an entry prove, in the database, that it shares its own player's speech from its own round.
alter table public.speeches add constraint speeches_entry_key unique (id, player_id, community_round_id);

-- The publication record: one shared speech per speaker per round. Withdrawing keeps the row,
-- so the speaker can share the same speech again but never swap in a different one.
create table public.community_entries (
  id            uuid primary key default gen_random_uuid(),
  round_id      uuid not null references public.community_rounds (id),
  speech_id     uuid not null,
  player_id     uuid not null references public.profiles (id),
  shared_at     timestamptz not null default now(),
  withdrawn_at  timestamptz,
  constraint community_entries_one_per_round unique (round_id, player_id),
  constraint community_entries_speech_key unique (speech_id),
  constraint community_entries_own_speech foreign key (speech_id, player_id, round_id)
    references public.speeches (id, player_id, community_round_id) on delete cascade
);

-- One reaction per account per speech. Changing the emoji changes its kind, never its weight.
create table public.speech_reactions (
  speech_id   uuid not null references public.community_entries (speech_id) on delete cascade,
  user_id     uuid not null references public.profiles (id) on delete cascade,
  reaction    text not null check (reaction in ('love', 'like', 'fire', 'amazing')),
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now(),
  primary key (speech_id, user_id)
);

-- Reaction requests per account in the current minute (changes and removals included).
create table public.community_reaction_limits (
  user_id       uuid primary key references public.profiles (id) on delete cascade,
  window_start  timestamptz not null,
  requests      int not null
);

create index community_entries_round_idx on public.community_entries (round_id) where withdrawn_at is null;
create index speech_reactions_user_idx on public.speech_reactions (user_id);
create index speeches_community_round_idx on public.speeches (community_round_id, player_id)
  where community_round_id is not null;
create index speeches_audio_expiry_idx on public.speeches (audio_expires_at)
  where community_round_id is not null and audio_path is not null;

alter table public.community_rounds          enable row level security;
alter table public.community_entries         enable row level security;
alter table public.speech_reactions          enable row level security;
alter table public.community_reaction_limits enable row level security;

-- The single definition of "the community may see and hear this": the round is open, the
-- entry is shared, review approved it, it has a grade, and its recording still exists.
-- Rankings, playback and reactions all go through it, so they can never disagree.
create function public.community_eligible_entries(p_round uuid)
returns table (entry_id uuid, speech_id uuid, player_id uuid, shared_at timestamptz,
               side text, overall numeric, letter text, display_name text)
language sql stable
set search_path = ''
as $$
  select e.id, e.speech_id, e.player_id, e.shared_at, s.side, s.overall,
         s.report -> 'overall' ->> 'letter', p.display_name
  from public.community_entries e
  join public.community_rounds r on r.id = e.round_id
  join public.speeches s on s.id = e.speech_id
  join public.profiles p on p.id = e.player_id
  where e.round_id = p_round
    and e.withdrawn_at is null
    and now() >= r.starts_at and now() < r.ends_at
    and s.status = 'done' and s.overall is not null
    and s.moderation_status = 'approved'
    and s.audio_path is not null and s.audio_deleted_at is null
    and (s.audio_expires_at is null or s.audio_expires_at > now());
$$;

-- One page of a round's board, ranked in the database. 'grade': score, then earlier share.
-- 'favorites': distinct reacting accounts, then the same order. Ranks are absolute across
-- pages. Returns counts and the caller's own reaction, never who reacted, reports or paths.
create function public.community_board(p_round uuid, p_user uuid, p_sort text, p_limit int, p_offset int)
returns jsonb
language sql stable
set search_path = ''
as $$
  with tallied as (
    select c.entry_id, c.speech_id, c.player_id, c.shared_at, c.side, c.overall, c.letter, c.display_name,
           count(x.user_id) filter (where x.reaction = 'love')    as love,
           count(x.user_id) filter (where x.reaction = 'like')    as likes,
           count(x.user_id) filter (where x.reaction = 'fire')    as fire,
           count(x.user_id) filter (where x.reaction = 'amazing') as amazing,
           count(x.user_id)                                       as total,
           max(x.reaction) filter (where x.user_id = p_user)      as mine
    from public.community_eligible_entries(p_round) c
    left join public.speech_reactions x on x.speech_id = c.speech_id
    group by c.entry_id, c.speech_id, c.player_id, c.shared_at, c.side, c.overall, c.letter, c.display_name
  ), ranked as (
    select t.*, row_number() over (
      order by case when p_sort = 'favorites' then t.total end desc nulls last,
               t.overall desc, t.shared_at asc, t.entry_id asc) as place
    from tallied t
  ), page as (
    select * from ranked order by place
    limit greatest(0, least(coalesce(p_limit, 20), 101)) offset greatest(0, coalesce(p_offset, 0))
  )
  select coalesce(jsonb_agg(jsonb_build_object(
    'id', entry_id, 'speech_id', speech_id, 'rank', place, 'display_name', display_name, 'side', side,
    'overall', overall, 'letter', letter,
    'counts', jsonb_build_object('love', love, 'like', likes, 'fire', fire, 'amazing', amazing),
    'total_reactions', total, 'my_reaction', mine, 'is_owner', player_id = p_user) order by place), '[]'::jsonb)
  from page;
$$;

-- Where to find a shared recording, or null when the community may not hear it.
create function public.community_audio(p_speech uuid)
returns jsonb
language sql stable
set search_path = ''
as $$
  select jsonb_build_object('audio_path', s.audio_path, 'ends_at', r.ends_at)
  from public.community_entries e
  join public.community_rounds r on r.id = e.round_id
  join public.speeches s on s.id = e.speech_id
  where e.speech_id = p_speech
    and exists (select 1 from public.community_eligible_entries(e.round_id) c where c.speech_id = p_speech);
$$;

-- Set, change or remove (p_reaction null) the caller's reaction, in one transaction.
-- The entry row is locked first, so a withdrawal, the round closing or a second click
-- cannot slip between the checks and the write. Totals are always counted, never added to.
-- Returns {status}: ok (with counts), not_found, closed, own, invalid or limited.
create function public.community_react(p_speech uuid, p_user uuid, p_reaction text, p_per_minute int default 60)
returns jsonb
language plpgsql
set search_path = ''
as $$
declare
  v_entry record;
  v_requests int;
begin
  if p_reaction is not null and p_reaction not in ('love', 'like', 'fire', 'amazing') then
    return jsonb_build_object('status', 'invalid');
  end if;

  select e.round_id, e.player_id, r.ends_at into v_entry
  from public.community_entries e
  join public.community_rounds r on r.id = e.round_id
  where e.speech_id = p_speech
  for update of e;
  if not found then
    return jsonb_build_object('status', 'not_found');
  end if;
  if now() >= v_entry.ends_at then
    return jsonb_build_object('status', 'closed');
  end if;
  if not exists (select 1 from public.community_eligible_entries(v_entry.round_id) c where c.speech_id = p_speech) then
    return jsonb_build_object('status', 'not_found');
  end if;
  if v_entry.player_id = p_user then
    return jsonb_build_object('status', 'own');
  end if;

  insert into public.community_reaction_limits as l (user_id, window_start, requests)
  values (p_user, date_trunc('minute', now()), 1)
  on conflict (user_id) do update
    set requests = case when l.window_start = excluded.window_start then l.requests + 1 else 1 end,
        window_start = excluded.window_start
  returning l.requests into v_requests;
  if v_requests > p_per_minute then
    return jsonb_build_object('status', 'limited', 'retry_after',
      greatest(1, ceil(extract(epoch from date_trunc('minute', now()) + interval '1 minute' - clock_timestamp()))::int));
  end if;

  if p_reaction is null then
    delete from public.speech_reactions where speech_id = p_speech and user_id = p_user;
  else
    insert into public.speech_reactions as x (speech_id, user_id, reaction)
    values (p_speech, p_user, p_reaction)
    on conflict (speech_id, user_id) do update
      set reaction = excluded.reaction, updated_at = now()
      where x.reaction is distinct from excluded.reaction;
  end if;

  return (
    select jsonb_build_object('status', 'ok',
      'counts', jsonb_build_object(
        'love',    count(*) filter (where x.reaction = 'love'),
        'like',    count(*) filter (where x.reaction = 'like'),
        'fire',    count(*) filter (where x.reaction = 'fire'),
        'amazing', count(*) filter (where x.reaction = 'amazing')),
      'total_reactions', count(*),
      'my_reaction', max(x.reaction) filter (where x.user_id = p_user))
    from public.speech_reactions x
    where x.speech_id = p_speech);
end;
$$;

-- Supabase lets the public and account roles call new functions by default. These four
-- take a user id as a plain argument, so only the server may call them.
revoke all on function public.community_eligible_entries(uuid)              from public, anon, authenticated;
revoke all on function public.community_board(uuid, uuid, text, int, int)   from public, anon, authenticated;
revoke all on function public.community_audio(uuid)                         from public, anon, authenticated;
revoke all on function public.community_react(uuid, uuid, text, int)        from public, anon, authenticated;
grant execute on function public.community_eligible_entries(uuid)            to service_role;
grant execute on function public.community_board(uuid, uuid, text, int, int) to service_role;
grant execute on function public.community_audio(uuid)                       to service_role;
grant execute on function public.community_react(uuid, uuid, text, int)      to service_role;
