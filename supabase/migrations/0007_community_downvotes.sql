-- Extend existing reactions without rewriting migration 0006 or deleting votes.
-- One choice per listener: positive emoji +1, downvote -1, removal 0.
-- Total reactions remains the number of listeners; favorites sorts by the net score.
alter table public.speech_reactions drop constraint speech_reactions_reaction_check;
alter table public.speech_reactions add constraint speech_reactions_reaction_check
  check (reaction in ('love', 'like', 'fire', 'amazing', 'downvote'));

-- Keep the existing eligibility, tie breakers, pagination, locks and mutation limits.
create or replace function public.community_board(p_round uuid, p_user uuid, p_sort text, p_limit int, p_offset int)
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
           count(x.user_id) filter (where x.reaction = 'downvote') as downvotes,
           count(x.user_id) - 2 * count(x.user_id) filter (where x.reaction = 'downvote') as score,
           count(x.user_id)                                       as total,
           max(x.reaction) filter (where x.user_id = p_user)      as mine
    from public.community_eligible_entries(p_round) c
    left join public.speech_reactions x on x.speech_id = c.speech_id
    group by c.entry_id, c.speech_id, c.player_id, c.shared_at, c.side, c.overall, c.letter, c.display_name
  ), ranked as (
    select t.*, row_number() over (
      order by case when p_sort = 'favorites' then t.score end desc nulls last,
               t.overall desc, t.shared_at asc, t.entry_id asc) as place
    from tallied t
  ), page as (
    select * from ranked order by place
    limit greatest(0, least(coalesce(p_limit, 20), 101)) offset greatest(0, coalesce(p_offset, 0))
  )
  select coalesce(jsonb_agg(jsonb_build_object(
    'id', entry_id, 'speech_id', speech_id, 'rank', place, 'display_name', display_name, 'side', side,
    'overall', overall, 'letter', letter,
    'counts', jsonb_build_object('love', love, 'like', likes, 'fire', fire, 'amazing', amazing, 'downvote', downvotes),
    'total_reactions', total, 'reaction_score', score, 'my_reaction', mine, 'is_owner', player_id = p_user) order by place), '[]'::jsonb)
  from page;
$$;

create or replace function public.community_react(p_speech uuid, p_user uuid, p_reaction text, p_per_minute int default 60)
returns jsonb
language plpgsql
set search_path = ''
as $$
declare
  v_entry record;
  v_requests int;
begin
  if p_reaction is not null and p_reaction not in ('love', 'like', 'fire', 'amazing', 'downvote') then
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
        'amazing', count(*) filter (where x.reaction = 'amazing'),
        'downvote', count(*) filter (where x.reaction = 'downvote')),
      'total_reactions', count(*),
      'reaction_score', count(*) - 2 * count(*) filter (where x.reaction = 'downvote'),
      'my_reaction', max(x.reaction) filter (where x.user_id = p_user))
    from public.speech_reactions x
    where x.speech_id = p_speech);
end;
$$;

-- CREATE OR REPLACE preserves grants; reaffirm that only the server can call these.
revoke all on function public.community_board(uuid, uuid, text, int, int) from public, anon, authenticated;
revoke all on function public.community_react(uuid, uuid, text, int) from public, anon, authenticated;
grant execute on function public.community_board(uuid, uuid, text, int, int) to service_role;
grant execute on function public.community_react(uuid, uuid, text, int) to service_role;
