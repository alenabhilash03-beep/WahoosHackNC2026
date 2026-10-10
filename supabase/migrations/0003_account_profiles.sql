-- Auth owns passwords. Create the existing player profile inside the signup transaction.
-- Keep the original server-only table access model (no client RLS policies).
create or replace function public.handle_new_account()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  insert into public.profiles (id, display_name)
  values (new.id, coalesce(nullif(left(btrim(new.raw_user_meta_data ->> 'display_name'), 40), ''), 'player'))
  on conflict (id) do nothing;
  return new;
end;
$$;
revoke all on function public.handle_new_account() from public, anon, authenticated;
drop trigger if exists on_auth_account_created on auth.users;
create trigger on_auth_account_created
after insert on auth.users
for each row execute function public.handle_new_account();

insert into public.profiles (id, display_name)
select id, coalesce(nullif(left(btrim(raw_user_meta_data ->> 'display_name'), 40), ''), 'player')
from auth.users
on conflict (id) do nothing;
