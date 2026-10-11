import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {readFile, readdir} from 'node:fs/promises';
import {after, before, test} from 'node:test';
import {PGlite} from '@electric-sql/pglite';
import pg from 'pg';

const migrations = new URL('../../supabase/migrations/', import.meta.url);
const url = process.env.DATABASE_TEST_URL;
let db, pool;

async function apply(database, names) {
  for (const name of names) {
    let sql = await readFile(new URL(name, migrations), 'utf8');
    // PGlite lacks pgcrypto; these migrations only use the built-in gen_random_uuid().
    sql = sql.replace('create extension if not exists pgcrypto;', '');
    await database.exec(sql);
  }
}

async function embedded() {
  const database = new PGlite();
  // Supabase-owned schemas/roles are infrastructure, not application migrations.
  await database.exec(`
    create schema auth;
    create table auth.users (id uuid primary key);
    create schema storage;
    create table storage.buckets (id text primary key, name text, public boolean);
    create role anon;
    create role authenticated;
    create role service_role bypassrls;
    grant usage on schema public to anon, authenticated, service_role;
    alter default privileges in schema public grant all on tables to anon, authenticated, service_role;
  `);
  return database;
}

before(async () => {
  if (url) {
    const parsed = new URL(url);
    assert.ok(['localhost', '127.0.0.1', '[::1]'].includes(parsed.hostname),
      'DATABASE_TEST_URL must point to a disposable local database; never production');
    pool = new pg.Pool({connectionString: url});
    db = await pool.connect();
  } else {
    db = await embedded();
    await apply(db, (await readdir(migrations)).filter(n => n.endsWith('.sql')).sort());
  }
});

after(async () => {
  if (pool) { db.release(); await pool.end(); }
  else if (db) await db.close();
});

async function transaction(run) {
  await db.query('begin');
  try { await run(); }
  finally { await db.query('rollback'); }
}

async function rejects(sql, params, code) {
  await db.query('savepoint invalid_write');
  try { await assert.rejects(db.query(sql, params), error => error.code === code); }
  finally { await db.query('rollback to savepoint invalid_write'); }
}

async function fixture() {
  const owner = randomUUID(), other = randomUUID(), motion = randomUUID(), speech = randomUUID();
  await db.query('insert into auth.users (id) values ($1), ($2)', [owner, other]);
  await db.query('insert into public.profiles (id) values ($1), ($2)', [owner, other]);
  await db.query('insert into public.motions (id, text) values ($1, $2)', [motion, `test-${motion}`]);
  await db.query(`insert into public.speeches (id, player_id, motion_id, side, audio_path)
    values ($1, $2, $3, 'yes', $4)`, [speech, owner, motion, `${owner}/${speech}.wav`]);
  return {owner, other, motion, speech};
}

test('every application table has RLS, no browser policies, and the audio bucket is private', async () => {
  const tables = ['profiles', 'scenarios', 'sessions', 'turns', 'cards', 'ranks', 'motions', 'speeches',
    'judgments', 'gold_pairs', 'arena_scores', 'matches', 'match_players', 'match_speeches', 'match_results', 'ratings',
    'community_rounds', 'community_entries', 'speech_reactions', 'community_reaction_limits'];
  const rows = (await db.query(`select relname, relrowsecurity from pg_class c
    join pg_namespace n on n.oid = c.relnamespace where n.nspname = 'public' and relkind = 'r'`)).rows;
  for (const name of tables) assert.equal(rows.find(r => r.relname === name)?.relrowsecurity, true, name);
  assert.equal((await db.query(`select count(*)::int as n from pg_policies
    where schemaname = 'public' and tablename = any($1::text[])`, [tables])).rows[0].n, 0);
  assert.equal((await db.query(`select public from storage.buckets where id = 'audio'`)).rows[0]?.public, false);
});

test('practice prompts are seeded and motion difficulty/featured-date constraints work', async () => transaction(async () => {
  const prompts = (await db.query(`select text from public.motions where active`)).rows.map(r => r.text);
  for (const text of ['Should the frontier of AI be paced?', 'Should homework be banned in elementary school?',
    'Should public transportation be free?']) assert.ok(prompts.includes(text));
  const {motion} = await fixture();
  await rejects('update public.motions set difficulty = $1 where id = $2', ['impossible', motion], '23514');
  await db.query(`update public.motions set featured_on = '2099-01-01' where id = $1`, [motion]);
  await rejects(`insert into public.motions (text, featured_on) values ($1, '2099-01-01')`, [randomUUID()], '23505');
}));

test('queued speech persists a full report, while invalid owner/side/status writes fail', async () => transaction(async () => {
  const {owner, other, motion, speech} = await fixture();
  const report = JSON.parse(await readFile(new URL('../../softskills-grader/sample_report.json', import.meta.url), 'utf8'));
  await db.query(`update public.speeches set status = 'grading' where id = $1`, [speech]);
  await db.query(`update public.speeches set status = 'done', report = $2, overall = $3, standards_id = $4 where id = $1`,
    [speech, JSON.stringify(report), report.overall.score, report.inputs.standards_id]);
  const rows = (await db.query('select * from public.speeches where player_id = $1 order by created_at, id', [owner])).rows;
  assert.equal(rows.length, 1);
  assert.deepEqual(rows[0].report, report);
  assert.equal(Number(rows[0].overall), report.overall.score);
  assert.equal(rows[0].status, 'done');
  assert.equal(rows[0].published, false);
  assert.equal(rows[0].ranked, false);
  assert.equal(rows[0].moderation_status, 'pending');
  assert.equal((await db.query('select id from public.speeches where id = $1 and player_id = $2', [speech, other])).rows.length, 0);
  await rejects(`update public.speeches set side = 'maybe' where id = $1`, [speech], '23514');
  await rejects(`update public.speeches set status = 'invented' where id = $1`, [speech], '23514');
  await rejects(`insert into public.speeches (player_id, motion_id, side, audio_path) values ($1, $2, 'yes', 'x')`,
    [randomUUID(), motion], '23503');
}));

test('default names may repeat, named profiles are unique regardless of case, invalid names fail', async () => transaction(async () => {
  const {owner, other} = await fixture();
  await db.query(`update public.profiles set display_name = 'Ada_1' where id = $1`, [owner]);
  await rejects(`update public.profiles set display_name = 'ada_1' where id = $1`, [other], '23505');
  for (const name of ['ab', 'has space', 'Player', 'x'.repeat(21)]) {
    await rejects(`update public.profiles set display_name = $1 where id = $2`, [name, other], '23514');
  }
}));

test('anon and authenticated roles cannot read or write application data; service role can', async () => transaction(async () => {
  const {owner} = await fixture();
  for (const role of ['anon', 'authenticated']) {
    await db.query(`set local role ${role}`);
    for (const table of ['profiles', 'motions', 'speeches']) {
      assert.equal((await db.query(`select * from public.${table}`)).rows.length, 0, `${role} ${table}`);
    }
    await rejects(`insert into public.motions (text) values ($1)`, [randomUUID()], '42501');
    await db.query('reset role');
  }
  await db.query('set local role service_role');
  assert.equal((await db.query(`select id from public.profiles where id = $1`, [owner])).rows.length, 1);
}));

test('community rounds, entries and reactions enforce their rules in the database', async () => transaction(async () => {
  const {owner, other, motion, speech} = await fixture();
  const round = (await db.query(`insert into public.community_rounds (motion_id, starts_at, ends_at)
    values ($1, now() - interval '1 hour', now() + interval '1 hour') returning id`, [motion])).rows[0].id;
  // One open round at a time, and a window that runs forwards.
  await rejects(`insert into public.community_rounds (motion_id, starts_at, ends_at) values ($1, now(), now() + interval '1 minute')`, [motion], '23P01');
  await rejects(`insert into public.community_rounds (motion_id, starts_at, ends_at) values ($1, now() + interval '3 days', now() + interval '2 days')`, [motion], '23514');
  await db.query(`insert into public.community_rounds (motion_id, starts_at, ends_at) values ($1, now() + interval '1 hour', now() + interval '2 hours')`, [motion]);

  // An entry must be its own player's speech, recorded for that round; one per player per round.
  const entry = `insert into public.community_entries (round_id, speech_id, player_id) values ($1, $2, $3)`;
  await rejects(entry, [round, speech, owner], '23503');                       // private practice: not tagged with a round
  const graded = {gradable: true, overall: {score: 90, letter: 'A-'}, transcript: 'private words'};
  await db.query(`update public.speeches set community_round_id = $1, status = 'done', overall = 90, report = $2 where id = $3`, [round, graded, speech]);
  await rejects(entry, [round, speech, other], '23503');                       // somebody else's speech
  await db.query(entry, [round, speech, owner]);
  const second = randomUUID();
  await db.query(`insert into public.speeches (id, player_id, motion_id, side, audio_path, community_round_id) values ($1, $2, $3, 'no', 'x.wav', $4)`, [second, owner, motion, round]);
  await rejects(entry, [round, second, owner], '23505');                       // a different speech in the same round
  await rejects(entry, [round, speech, owner], '23505');

  const board = async (user, sort = 'grade') => (await db.query(`select public.community_board($1, $2, $3, 20, 0) as rows`, [round, user, sort])).rows[0].rows;
  const react = async (user, reaction, limit = 60) => (await db.query(`select public.community_react($1, $2, $3, $4) as r`, [speech, user, reaction, limit])).rows[0].r;
  // Shared but not reviewed: invisible, silent and not reactable.
  assert.deepEqual(await board(other), []);
  assert.equal((await db.query(`select public.community_audio($1) as a`, [speech])).rows[0].a, null);
  assert.equal((await react(other, 'fire')).status, 'not_found');
  await db.query(`update public.speeches set moderation_status = 'approved' where id = $1`, [speech]);

  const [row] = await board(other);
  assert.deepEqual(row.counts, {love: 0, like: 0, fire: 0, amazing: 0});
  assert.deepEqual([row.rank, row.total_reactions, row.my_reaction, row.is_owner, row.letter, Number(row.overall)], [1, 0, null, false, 'A-', 90]);
  assert.deepEqual(Object.keys(row).sort(), ['counts', 'display_name', 'id', 'is_owner', 'letter', 'my_reaction', 'overall', 'rank', 'side', 'speech_id', 'total_reactions']);
  assert.equal((await board(owner))[0].is_owner, true);
  assert.equal((await db.query(`select public.community_audio($1) as a`, [speech])).rows[0].a.audio_path, `${owner}/${speech}.wav`);

  // One reaction per account: set, repeat, change, remove. Never your own speech, never an unknown emoji.
  assert.deepEqual([(await react(other, 'fire')).total_reactions, (await react(other, 'fire')).total_reactions], [1, 1]);
  const changed = await react(other, 'amazing');
  assert.deepEqual([changed.status, changed.total_reactions, changed.my_reaction, changed.counts.fire, changed.counts.amazing], ['ok', 1, 'amazing', 0, 1]);
  assert.equal((await board(other, 'favorites'))[0].my_reaction, 'amazing');
  assert.equal((await react(owner, 'love')).status, 'own');
  assert.equal((await react(other, 'clap')).status, 'invalid');
  assert.equal((await react(other, 'love', 0)).status, 'limited');
  assert.equal((await db.query(`select public.community_react($1, $2, 'love', 60) as r`, [randomUUID(), other])).rows[0].r.status, 'not_found');
  await rejects(`insert into public.speech_reactions (speech_id, user_id, reaction) values ($1, $2, 'clap')`, [speech, owner], '23514');
  await rejects(`insert into public.speech_reactions (speech_id, user_id, reaction) values ($1, $2, 'love')`, [speech, other], '23505');
  assert.deepEqual([(await react(other, null)).total_reactions, (await react(other, null)).status], [0, 'ok']);

  // Withdrawn, expired or closed: gone from the board; the reaction says why.
  await react(other, 'like');
  await db.query(`update public.community_entries set withdrawn_at = now() where speech_id = $1`, [speech]);
  assert.deepEqual([await board(other), (await react(other, 'fire')).status], [[], 'not_found']);
  await db.query(`update public.community_entries set withdrawn_at = null where speech_id = $1`, [speech]);
  assert.equal((await board(other))[0].total_reactions, 1);
  await db.query(`update public.speeches set audio_expires_at = now() - interval '1 second' where id = $1`, [speech]);
  assert.deepEqual(await board(other), []);
  await db.query(`update public.speeches set audio_expires_at = null where id = $1`, [speech]);
  await db.query(`delete from public.community_rounds where id <> $1`, [round]);
  await db.query(`update public.community_rounds set starts_at = now() - interval '2 hours', ends_at = now() - interval '1 minute' where id = $1`, [round]);
  assert.deepEqual([await board(other), (await react(other, 'fire')).status], [[], 'closed']);

  // The public and account roles can neither call the functions nor see the rows.
  for (const role of ['anon', 'authenticated']) {
    await db.query(`set local role ${role}`);
    await rejects(`select public.community_board($1, $2, 'grade', 20, 0)`, [round, other], '42501');
    await rejects(`select public.community_react($1, $2, 'love', 60)`, [speech, other], '42501');
    await rejects(`select public.community_audio($1)`, [speech], '42501');
    await rejects(`select * from public.community_eligible_entries($1)`, [round], '42501');
    for (const table of ['community_rounds', 'community_entries', 'speech_reactions', 'community_reaction_limits']) {
      assert.equal((await db.query(`select * from public.${table}`)).rows.length, 0, `${role} ${table}`);
    }
    await rejects(`insert into public.speech_reactions (speech_id, user_id, reaction) values ($1, $2, 'love')`, [speech, owner], '42501');
    await db.query('reset role');
  }
  // Deleting a speech takes its entry and reactions with it.
  await db.query(`delete from public.speeches where id = $1`, [speech]);
  assert.equal((await db.query(`select count(*)::int as n from public.community_entries where speech_id = $1`, [speech])).rows[0].n, 0);
  assert.equal((await db.query(`select count(*)::int as n from public.speech_reactions where speech_id = $1`, [speech])).rows[0].n, 0);
}));

test('incremental upgrade preserves old names/speeches and repairs an existing public audio bucket', async () => {
  const upgrade = await embedded();
  try {
    const names = (await readdir(migrations)).filter(n => n.endsWith('.sql')).sort();
    await apply(upgrade, names.filter(n => n < '0005'));
    const id = randomUUID(), motion = randomUUID();
    await upgrade.query('insert into auth.users (id) values ($1)', [id]);
    await upgrade.query(`insert into public.profiles (id, display_name) values ($1, 'legacy name')`, [id]);
    await upgrade.query(`insert into public.motions (id, text) values ($1, 'Legacy prompt')`, [motion]);
    await upgrade.query(`insert into public.speeches (player_id, motion_id, side, audio_path) values ($1, $2, 'no', 'legacy.wav')`, [id, motion]);
    await upgrade.query(`update storage.buckets set public = true where id = 'audio'`);
    await apply(upgrade, names.filter(n => n >= '0005'));
    assert.equal((await upgrade.query('select display_name from public.profiles where id = $1', [id])).rows[0].display_name, 'legacy name');
    assert.equal((await upgrade.query('select audio_path from public.speeches where player_id = $1', [id])).rows[0].audio_path, 'legacy.wav');
    assert.equal((await upgrade.query(`select public from storage.buckets where id = 'audio'`)).rows[0].public, false);
  } finally { await upgrade.close(); }
});

test('concurrent case-insensitive name claims allow exactly one winner', {skip: !url && 'Requires local Supabase/Postgres (DATABASE_TEST_URL)'}, async () => {
  const users = [randomUUID(), randomUUID()];
  const name = `n_${randomUUID().replaceAll('-', '').slice(0, 16)}`;
  try {
    await db.query('insert into auth.users (id) values ($1), ($2)', users);
    await db.query('insert into public.profiles (id) values ($1), ($2)', users);
    const results = await Promise.allSettled(users.map((id, i) => pool.query(
      'update public.profiles set display_name = $1 where id = $2', [i ? name.toUpperCase() : name, id])));
    assert.equal(results.filter(r => r.status === 'fulfilled').length, 1);
    const rejected = results.find(r => r.status === 'rejected');
    assert.equal(rejected.reason.code, '23505');
  } finally {
    await db.query('delete from auth.users where id = any($1::uuid[])', [users]);
  }
});
