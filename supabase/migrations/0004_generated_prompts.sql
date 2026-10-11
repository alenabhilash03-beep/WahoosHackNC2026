-- Prompts generated in the game from a category and difficulty; apply after 0003.
alter table public.motions add column if not exists category text;
alter table public.motions add column if not exists difficulty text
  check (difficulty is null or difficulty in ('beginner', 'intermediate', 'expert'));
