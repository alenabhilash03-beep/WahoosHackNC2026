"""Review speeches shared with the community. A shared speech stays hidden until approved.

Run from the repository root, with the project URL and server key in server/.env:

    python -m server.scripts.review_community                    # list what is waiting
    python -m server.scripts.review_community --approve <speech id> [...]
    python -m server.scripts.review_community --reject <speech id> [...]

The list prints a five-minute private link for each recording so the reviewer can listen.
Approving shows the speech on the community board while its round is open. Rejecting
hides it for good and the speaker sees that on their card. Links are not logged anywhere;
treat the terminal output as private. With COMMUNITY_AUTO_APPROVE=true the server approves
at share time and nothing waits here.
"""
import argparse

from ..app.settings import Settings


def main(argv=None, repository=None):
    parser = argparse.ArgumentParser(description='Approve or reject speeches shared with the community.')
    parser.add_argument('--approve', nargs='+', default=[], metavar='SPEECH_ID')
    parser.add_argument('--reject', nargs='+', default=[], metavar='SPEECH_ID')
    args = parser.parse_args(argv)
    if set(args.approve) & set(args.reject):
        parser.error('The same speech cannot be approved and rejected.')
    if repository is None:
        settings = Settings.from_env()
        if not settings.supabase_url or not settings.supabase_service_role_key:
            parser.error('Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in server/.env.')
        from ..app.repository import SupabaseRepository
        repository = SupabaseRepository(settings)
    missing = 0
    for status, ids in (('approved', args.approve), ('rejected', args.reject)):
        for speech_id in ids:
            found = repository.review(speech_id, status)
            missing += not found
            print(f"{speech_id}  {status if found else 'not found'}")
    if not args.approve and not args.reject:
        waiting = repository.pending_reviews()
        for item in waiting:
            link = repository.signed_audio(item['audio_path']) if item.get('audio_path') else 'recording expired'
            print(f"{item['speech_id']}  {item.get('display_name') or 'player'}  {str(item.get('side')).upper()}  "
                  f"{item.get('overall')} / 100  shared {item['shared_at']}\n    listen: {link}")
        print(f'{len(waiting)} shared speech(es) waiting for review.')
    return 1 if missing else 0


if __name__ == '__main__':
    raise SystemExit(main())
