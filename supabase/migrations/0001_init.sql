-- NetworkMaxx schema, all phases. Apply once: supabase db push  (or paste into the SQL editor).
-- Clients never read these tables directly: every read and write goes through the FastAPI
-- server with the service-role key. RLS is enabled with no policies, so the anon key can
-- touch nothing. Live updates go over Realtime broadcast channels, not table subscriptions.

create extension if not exists pgcrypto;

-- ---------------------------------------------------------------- players
create table if not exists public.profiles (
  id            uuid primary key references auth.users (id) on delete cascade,
  display_name  text not null default 'player',
  created_at    timestamptz not null default now()
);

-- ------------------------------------------------- conversation game (phases 1-4)
create table if not exists public.scenarios (
  id      text primary key,                      -- e.g. alumni_coffee_chat
  mode    text not null,                         -- coffee_chat | interview_star | rizz
  config  jsonb not null,                        -- the parsed scenario YAML
  active  boolean not null default true
);

create table if not exists public.sessions (
  id                   uuid primary key default gen_random_uuid(),
  user_id              uuid not null references public.profiles (id),
  scenario_id          text not null references public.scenarios (id),
  conversation_id      text unique,              -- ElevenLabs conversation id, set by /bind
  status               text not null default 'created'
                       check (status in ('created', 'live', 'ended', 'grading', 'done', 'failed')),
  outcome              text check (outcome in ('won', 'lost', 'timeout')),
  rapport              numeric not null default 0,
  declared_main_point  text,
  audio_path           text,                     -- storage path of the user's mic recording
  persona_turns        jsonb,                    -- [{start, end, text}] on the recording clock
  started_at           timestamptz not null default now(),
  ended_at             timestamptz
);

create table if not exists public.turns (
  session_id     uuid not null references public.sessions (id) on delete cascade,
  idx            int not null,
  speaker        text not null check (speaker in ('user', 'agent')),
  text           text not null,
  rapport_after  numeric,
  delta          numeric,
  badge          text,
  reason         text,
  created_at     timestamptz not null default now(),
  primary key (session_id, idx)
);

create table if not exists public.cards (
  session_id      uuid primary key references public.sessions (id) on delete cascade,
  overall         numeric not null,
  stats           jsonb not null,
  metrics         jsonb not null,
  events          jsonb not null,
  judged          jsonb,
  engine_version  text not null,
  standards_id    text,
  created_at      timestamptz not null default now()
);

create table if not exists public.ranks (
  player_id        uuid not null references public.profiles (id),
  mode             text not null,
  rolling_overall  numeric not null default 0,
  tier             text not null default 'Bronze',
  xp               int not null default 0,
  primary key (player_id, mode)
);

-- -------------------------------------------------------------- debate (phase 5)
create table if not exists public.motions (
  id      uuid primary key default gen_random_uuid(),
  text    text not null unique,
  active  boolean not null default true
);

create table if not exists public.speeches (
  id                 uuid primary key default gen_random_uuid(),
  player_id          uuid not null references public.profiles (id),
  motion_id          uuid not null references public.motions (id),
  side               text not null check (side in ('yes', 'no')),
  round              text not null default 'opening' check (round in ('opening', 'rebuttal')),
  audio_path         text not null,
  masked_audio_path  text,
  report             jsonb,                      -- softskills-grader report, unchanged
  overall            numeric,
  standards_id       text,
  published          boolean not null default false,
  moderation_status  text not null default 'pending'
                     check (moderation_status in ('pending', 'approved', 'rejected')),
  created_at         timestamptz not null default now()
);
create index if not exists speeches_arena_idx on public.speeches (motion_id, published, side);

-- --------------------------------------------------- community judging (phase 7)
create table if not exists public.judgments (
  id              uuid primary key default gen_random_uuid(),
  rater_id        uuid not null references public.profiles (id),
  motion_id       uuid not null references public.motions (id),
  speech_a        uuid not null references public.speeches (id),
  speech_b        uuid not null references public.speeches (id),
  pre_stance      smallint not null check (pre_stance between -2 and 2),
  post_stance     smallint not null check (post_stance between -2 and 2),
  logos_pick      text not null check (logos_pick in ('a', 'b')),
  pathos_pick     text not null check (pathos_pick in ('a', 'b')),
  trust_a         boolean not null,
  trust_b         boolean not null,
  listened_a_pct  numeric not null,
  listened_b_pct  numeric not null,
  created_at      timestamptz not null default now(),
  unique (rater_id, speech_a, speech_b),
  check (speech_a <> speech_b)
);
create index if not exists judgments_motion_idx on public.judgments (motion_id, created_at);

create table if not exists public.gold_pairs (
  speech_a       uuid not null references public.speeches (id),
  speech_b       uuid not null references public.speeches (id),
  expected_pick  text not null check (expected_pick in ('a', 'b')),
  primary key (speech_a, speech_b)
);

create table if not exists public.arena_scores (
  speech_id           uuid primary key references public.speeches (id) on delete cascade,
  logos               numeric,
  pathos              numeric,
  ethos               numeric,
  persuasion          numeric,
  avg_shift           numeric,
  judgments           int not null default 0,
  provisional         boolean not null default true,
  settled             boolean not null default false,
  aggregator_version  text not null,
  computed_at         timestamptz not null default now()
);

-- ---------------------------------------------------------- matches (phase 6)
create table if not exists public.matches (
  id             uuid primary key default gen_random_uuid(),
  code           text not null unique,           -- 6-character join code
  mode           text not null check (mode in ('duel', 'team', 'ffa')),
  ranked         boolean not null default false,
  size           int not null check (size between 2 and 8),
  rebuttal       boolean not null default false,
  motion_id      uuid references public.motions (id),
  status         text not null default 'lobby'
                 check (status in ('lobby', 'prep', 'speaking', 'rebuttal_listen', 'rebuttal',
                                   'grading', 'done', 'cancelled')),
  phase_ends_at  timestamptz,
  standards_id   text,
  series_id      uuid,                           -- ranked duel: both games share one series id
  game           int not null default 1 check (game in (1, 2)),
  created_by     uuid not null references public.profiles (id),
  created_at     timestamptz not null default now()
);
create index if not exists matches_open_idx on public.matches (status, mode, ranked);
create index if not exists matches_series_idx on public.matches (series_id);

create table if not exists public.match_players (
  match_id   uuid not null references public.matches (id) on delete cascade,
  player_id  uuid not null references public.profiles (id),
  team       text,
  side       text check (side in ('yes', 'no')),
  seat       int not null,
  forfeited  boolean not null default false,
  joined_at  timestamptz not null default now(),
  primary key (match_id, player_id)
);

create table if not exists public.match_speeches (
  match_id   uuid not null references public.matches (id) on delete cascade,
  player_id  uuid not null references public.profiles (id),
  round      text not null check (round in ('opening', 'rebuttal')),
  speech_id  uuid not null references public.speeches (id),
  primary key (match_id, player_id, round)
);

create table if not exists public.match_results (
  match_id       uuid primary key references public.matches (id) on delete cascade,
  verdict        jsonb not null,                 -- match_judge.judge_match(...) output
  judge_version  text not null,
  judged_at      timestamptz not null default now()
);

create table if not exists public.ratings (
  player_id  uuid not null references public.profiles (id),
  mode       text not null check (mode in ('duel', 'team', 'ffa', 'debate_crowd')),
  rating     numeric not null default 1200,
  games      int not null default 0,
  primary key (player_id, mode)
);

-- ------------------------------------------------------------------ security
alter table public.profiles       enable row level security;
alter table public.scenarios      enable row level security;
alter table public.sessions       enable row level security;
alter table public.turns          enable row level security;
alter table public.cards          enable row level security;
alter table public.ranks          enable row level security;
alter table public.motions        enable row level security;
alter table public.speeches       enable row level security;
alter table public.judgments      enable row level security;
alter table public.gold_pairs     enable row level security;
alter table public.arena_scores   enable row level security;
alter table public.matches        enable row level security;
alter table public.match_players  enable row level security;
alter table public.match_speeches enable row level security;
alter table public.match_results  enable row level security;
alter table public.ratings        enable row level security;

-- Private bucket for every recording. The server hands out short-lived signed URLs.
insert into storage.buckets (id, name, public)
values ('audio', 'audio', false)
on conflict (id) do nothing;
