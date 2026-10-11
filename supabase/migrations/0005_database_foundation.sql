-- Server-only foundation: preserve existing speeches and accounts.
-- Fail on conflicting legacy names rather than silently renaming players.
-- The default 'player' is an unnamed profile and may occur more than once.
create unique index profiles_display_name_ci_key
  on public.profiles (lower(display_name))
  where display_name <> 'player';

alter table public.profiles add constraint profiles_display_name_check
  check (display_name = 'player' or
         (display_name ~ '^[A-Za-z0-9_]{3,20}$' and lower(display_name) <> 'player'))
  not valid;
-- NOT VALID preserves legacy rows, but enforces the rule on new/updated rows.

-- 0001 does nothing on conflict, which can preserve a previously public bucket.
-- Do not change size/MIME settings or delete objects in an existing bucket.
insert into storage.buckets (id, name, public)
values ('audio', 'audio', false)
on conflict (id) do update set public = false;
