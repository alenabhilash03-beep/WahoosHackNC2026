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
    'judgments', 'gold_pairs', 'arena_scores', 'matches', 'match_players', 'match_speeches', 'match_results', 'ratings'];
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
