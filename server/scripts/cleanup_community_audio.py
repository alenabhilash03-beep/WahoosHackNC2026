"""Delete the recordings of closed community rounds. Safe to run again and beside the server.

Run from the repository root, with the project URL and server key in server/.env:

    python -m server.scripts.cleanup_community_audio              # dry run: count only
    python -m server.scripts.cleanup_community_audio --apply
    python -m server.scripts.cleanup_community_audio --apply --limit 200

Only speeches recorded for a community round whose expiry (round end plus five minutes)
has passed are touched. Their file is deleted, then the shared entry and its reactions,
then the stored path. Grades, reports and progress stay. Private practice recordings are
never selected. A failed deletion keeps the path, so the next run retries it.
The running server does the same every few minutes; exit code 1 means some deletions failed.
"""
import argparse

from ..app.community import cleanup_audio
from ..app.settings import Settings


def main(argv=None, repository=None):
    parser = argparse.ArgumentParser(description='Delete expired community recordings.')
    parser.add_argument('--apply', action='store_true', help='delete (default: only count)')
    parser.add_argument('--limit', type=int, default=50, help='most recordings to handle in this run (default 50)')
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= 1000:
        parser.error('--limit must be between 1 and 1000.')
    if repository is None:
        settings = Settings.from_env()
        if not settings.supabase_url or not settings.supabase_service_role_key:
            parser.error('Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in server/.env.')
        from ..app.repository import SupabaseRepository
        repository = SupabaseRepository(settings)
    result = cleanup_audio(repository, limit=args.limit, dry_run=not args.apply)
    if args.apply:
        print(f"{result['removed']} expired recording(s) deleted, {result['failed']} failed (kept for the next run).")
    else:
        print(f"{result['expired']} expired recording(s) waiting (up to --limit). Dry run: nothing changed.")
    return 1 if result['failed'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
