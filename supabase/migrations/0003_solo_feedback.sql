-- Private solo processing state; apply after 0001 and 0002.
alter table public.speeches add column if not exists status text not null default 'queued';
alter table public.speeches add column if not exists error text;

update public.speeches
set status = case when (report->>'gradable')::boolean then 'done' else 'ungradable' end
where report is not null and status = 'queued';

do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'speeches_solo_status_check' and conrelid = 'public.speeches'::regclass) then
    alter table public.speeches add constraint speeches_solo_status_check
      check (status in ('queued', 'grading', 'done', 'ungradable', 'failed'));
  end if;
end $$;

create index if not exists speeches_owner_history_idx on public.speeches (player_id, created_at);
create index if not exists speeches_owner_prompt_side_idx on public.speeches (player_id, motion_id, side, created_at);

-- Practice prompts; daily rotation belongs to the community phase.
insert into public.motions (text) values
  ('Should the frontier of AI be paced?'),
  ('Should homework be banned in elementary school?'),
  ('Should public transportation be free?')
on conflict (text) do nothing;
