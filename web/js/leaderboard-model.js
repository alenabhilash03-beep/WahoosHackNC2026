// The same rule the server enforces (server/app/leaderboard.py).
export function nameProblem(value) {
  const name = String(value ?? '').trim();
  return /^[A-Za-z0-9_]{3,20}$/.test(name) && name.toLowerCase() !== 'player' ? '' : 'Names are 3 to 20 letters, digits or underscores.';
}

const speeches = n => `${n} speech${n === 1 ? '' : 'es'}`;

export function scoreLine(row) {
  return `${row.score} / 100${row.letter ? ` · ${row.letter}` : ''} · ${speeches(row.attempts)}`;
}

// What the "your place" panel says: not joined, joined without a grade, or ranked.
export function youLine(board) {
  if (!board.display_name) return 'Pick a name to put your best score on the board.';
  if (!board.you) return `You are ${board.display_name}. Record a graded speech to get on the board.`;
  return `You are ${board.display_name}: place ${board.you.place}, best ${scoreLine(board.you)}.`;
}
