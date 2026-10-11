"""Leaderboard: each named player's best graded speech, highest score first.

A player joins by choosing a display name. Until then the profile keeps the
schema default and stays off the board. The board shows names, scores and
letters only; transcripts, reports and recordings stay private to their owner.
"""
import re

UNNAMED = 'player'      # profiles.display_name default: not on the board
NAME = re.compile(r'[A-Za-z0-9_]{3,20}')


class NameTaken(Exception):
    pass


def clean_name(value):
    """The display name, or ValueError if it is not 3 to 20 letters, digits or underscores."""
    name = str(value or '').strip()
    if not NAME.fullmatch(name) or name.lower() == UNNAMED:
        raise ValueError('Names are 3 to 20 letters, digits or underscores.')
    return name


def board_row(row, display_name):
    """A stored speech as a board row, or None if it has no grade."""
    overall = (row.get('report') or {}).get('overall') or {}
    if row.get('status') not in ('done', 'graded') or (row.get('report') or {}).get('gradable') is not True:
        return None
    return dict(player_id=row['player_id'], display_name=display_name, score=overall['score'],
                letter=overall.get('letter'), created_at=row['created_at'])


def rank(rows, user, limit=50):
    """rows: one per graded speech, as {player_id, display_name, score, letter, created_at}.

    Tries are unlimited and a player's best counts; ties go to the earlier speech.
    Returns the top rows and the caller's own row (None if they are not on the board).
    """
    best, attempts = {}, {}
    for row in sorted(rows, key=lambda row: (-row['score'], row['created_at'])):
        best.setdefault(row['player_id'], row)
        attempts[row['player_id']] = attempts.get(row['player_id'], 0) + 1
    board = [dict(place=place, display_name=row['display_name'], score=row['score'], letter=row['letter'],
                  attempts=attempts[player], you=player == user)
             for place, (player, row) in enumerate(best.items(), 1)]
    return dict(rows=board[:limit], you=next((row for row in board if row['you']), None))
