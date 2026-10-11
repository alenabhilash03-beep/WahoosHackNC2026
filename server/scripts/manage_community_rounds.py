"""Create the community rounds for the coming days. Safe to run again: existing days are kept.

Run from the repository root, with the project URL and server key in server/.env:

    python -m server.scripts.manage_community_rounds                 # dry run: today and tomorrow
    python -m server.scripts.manage_community_rounds --days 7 --apply
    python -m server.scripts.manage_community_rounds --days 3 --motion <id> --motion <id> --apply

A round is one UTC day. A day without a round gets, in order of preference: the next
--motion id, that day's Daily Prompt, or the next hand-written prompt (no category) in
rotation. Today's round also becomes today's Daily Prompt. The running server does the
same for today and tomorrow every few minutes; use this to choose prompts ahead of time.
"""
import argparse

from ..app.community import ensure_rounds
from ..app.settings import Settings


def main(argv=None, repository=None):
    parser = argparse.ArgumentParser(description='Create community rounds for the coming UTC days.')
    parser.add_argument('--days', type=int, default=2, help='how many days from today (default 2)')
    parser.add_argument('--motion', action='append', default=[], metavar='ID',
                        help='prompt id for the next day that has no round; repeat for more days')
    parser.add_argument('--apply', action='store_true', help='create the rounds (default: only show the plan)')
    args = parser.parse_args(argv)
    if not 1 <= args.days <= 60:
        parser.error('--days must be between 1 and 60.')
    if repository is None:
        settings = Settings.from_env()
        if not settings.supabase_url or not settings.supabase_service_role_key:
            parser.error('Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in server/.env.')
        from ..app.repository import SupabaseRepository
        repository = SupabaseRepository(settings)
    plan = ensure_rounds(repository, days=args.days, motions=args.motion, apply=args.apply)
    for item in plan:
        state = ('created' if args.apply else 'would create') if item['created'] else 'exists'
        print(f"{item['day']}  {state:13}  prompt {item['motion_id']}")
    if not args.apply:
        print('Dry run: nothing changed. Add --apply to create the rounds.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
