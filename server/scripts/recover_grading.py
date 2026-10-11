"""Recover speeches whose grading was interrupted by a restart.

Run from the repository root, with the project URL and server key in server/.env:

    python -m server.scripts.recover_grading                            # dry run: count only
    python -m server.scripts.recover_grading --apply --worker-stopped   # mark them failed

Grading runs as in-process background tasks, so a restart leaves its speeches 'queued' or
'grading' for ever. This marks those rows 'failed'; their owners can then press RETRY SAVED
RECORDING on the card. It never grades anything itself and never touches a finished report,
a recording, or any row in another state.

Stop the server first. A row being graded right now looks exactly like an interrupted one,
so running --apply beside a live worker can fail a speech that is about to finish.
"""
import argparse

from ..app.settings import Settings

INTERRUPTED = 'Grading was interrupted. Retry the saved recording, or record again.'


def recover(repository, apply=False):
    """Count pending speeches; with apply, mark them failed. Returns (pending, changed)."""
    pending = repository.pending_count()
    return pending, repository.fail_pending(INTERRUPTED) if apply and pending else 0


def main(argv=None, repository=None):
    parser = argparse.ArgumentParser(description='Mark interrupted gradings failed so their owners can retry.')
    parser.add_argument('--apply', action='store_true', help='change the rows (default: only count them)')
    parser.add_argument('--worker-stopped', action='store_true',
                        help='confirm the server is stopped; required with --apply')
    args = parser.parse_args(argv)
    if args.apply and not args.worker_stopped:
        parser.error('--apply needs --worker-stopped: stop the server, then confirm with that flag.')
    if repository is None:
        settings = Settings.from_env()
        if not settings.supabase_url or not settings.supabase_service_role_key:
            parser.error('Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in server/.env.')
        from ..app.repository import SupabaseRepository
        repository = SupabaseRepository(settings)
    pending, changed = recover(repository, apply=args.apply)
    if args.apply:
        print(f'{changed} interrupted speech(es) marked failed. Restart the server; owners can retry on their cards.')
    else:
        print(f'{pending} speech(es) are queued or grading. Dry run: nothing changed.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
