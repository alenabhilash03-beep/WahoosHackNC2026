-- Prompt of the day and ranked attempts (Build Plan, stage 2). Apply after 0001_init.sql.

alter table public.motions  add column if not exists featured_on date unique;   -- one featured prompt per UTC day
alter table public.speeches add column if not exists ranked boolean not null default false;

create index if not exists speeches_board_idx
  on public.speeches (motion_id, ranked, published, overall desc);
