import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugins'))
from astrbot_plugin_persona_rsi.group_learning import GroupFeed, episodes, group_batch, GROUP_PLANNER, GROUP_REVIEWER, validate_group_proposal
from astrbot_plugin_persona_rsi.engine import EvolutionEngine
from astrbot_plugin_persona_rsi.policy import BEGIN, JUDGE, PLANNER, REVIEWER, compose
from astrbot_plugin_persona_rsi.storage import Store
from test_persona_rsi import FakeModels
from astrbot_plugin_persona_rsi.unified import UNIFIED_PLANNER, UNIFIED_REVIEWER

PLANNER = GROUP_PLANNER = UNIFIED_PLANNER
REVIEWER = GROUP_REVIEWER = UNIFIED_REVIEWER

GROUP = 'qq:GroupMessage:one'
OTHER = 'qq:GroupMessage:two'


def seed(store, session=GROUP, start=0):
    rows = []
    stamp = time.time() - 7000
    for occasion in range(6):
        for speaker in range(3):
            rows.append((str(start + occasion * 3 + speaker), session, 'person' + str((occasion + speaker) % 3),
                         stamp + occasion * 600 + speaker * 5, f'片段{occasion} 人类回复{speaker}', 'user'))
    store.record_group(rows, group_source_status='ready', group_allowed_sessions=[GROUP, OTHER])


def proposal_for(windows, scope='*'):
    chosen = [w for w in windows if scope == '*' or w['session'] == scope]
    examples = [{'window_id': w['id'], 'message_id': w['messages'][0]['id']} for w in chosen[:3]]
    if scope == '*':
        examples = []
        for session in sorted({w['session'] for w in chosen}):
            examples += [{'window_id': w['id'], 'message_id': w['messages'][0]['id']} for w in chosen if w['session'] == session][:3]
    return {'decision': 'propose', 'scope': scope, 'reason': '多人在不同场合用短句接梗', 'habits': [
        {'dimension': 'banter', 'text': '多人围绕同一句话接龙时，顺势复读一次或稍作变化接梗；话题转走就接新话题。',
         'evidence_ids': [e['window_id'] for e in examples], 'examples': examples}]}


class GroupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'state')

    async def asyncTearDown(self):
        self.store.close()
        self.temp.cleanup()

    async def test_feed_imports_history_once_and_excludes_bots_private_and_other_scope(self):
        path = Path(self.temp.name) / 'source.db'
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE messages(id INTEGER PRIMARY KEY, session_id,role,content,sender_id,group_id,platform,timestamp,metadata)')
            rows = [(1, GROUP, 'user', 'hello', 'a', 'one', 'aiocqhttp', time.time(), '{}'),
                    (2, GROUP, 'assistant', 'bot', 'self', 'one', 'aiocqhttp', time.time(), '{}'),
                    (3, GROUP, 'user', 'other bot', 'known_bot', 'one', 'aiocqhttp', time.time(), '{}'),
                    (4, OTHER, 'user', 'excluded group', 'b', 'two', 'aiocqhttp', time.time(), '{}'),
                    (5, 'qq:FriendMessage:a', 'user', 'private', 'a', '', 'aiocqhttp', time.time(), '{}'),
                    (6, GROUP, 'user', 'bot marked', 'bot2', 'one', 'aiocqhttp', time.time(), '{"is_bot_message":true}')]
            db.executemany('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?)', rows)
        db.close()
        async def allowed(s, p): return s == GROUP
        feed = GroupFeed(self.store, path, allowed, excluded_senders=['known_bot'])
        await feed.sync()
        await feed.sync()
        self.assertEqual(self.store.group_count(), 1)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM group_messages').fetchone()[0], 4)
        self.assertEqual(self.store.get('group_source_cursor'), 6)
        with sqlite3.connect(path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM messages').fetchone()[0], 6)
        db.close()
        # Later identification of a previously unknown bot invalidates imported samples.
        feed.excluded.add('a')
        await feed.sync()
        self.assertEqual(self.store.group_count(), 0)

    async def test_unknown_or_disabled_source_cannot_trigger_old_samples(self):
        seed(self.store)
        self.assertTrue(group_batch(self.store))
        async def allow(*_): return True
        await GroupFeed(self.store, Path(self.temp.name) / 'missing.db', allow).sync()
        self.assertEqual(group_batch(self.store), [])

    async def test_group_evidence_needs_different_people_and_occasions(self):
        seed(self.store)
        windows = group_batch(self.store)
        scope, habits = validate_group_proposal(proposal_for(windows), [], windows)
        self.assertEqual(scope, '*')
        self.assertEqual(habits[0]['scope'], '*')
        for w in windows:
            for m in w['messages']:
                m['speaker'] = 'same_spammer'
        with self.assertRaisesRegex(ValueError, 'multi-person'):
            validate_group_proposal(proposal_for(windows), [], windows)

    async def test_one_relay_cannot_be_split_into_three_occasions(self):
        seed(self.store)
        windows = group_batch(self.store)
        for i, w in enumerate(windows):
            w['created'] = 100 + i
        with self.assertRaisesRegex(ValueError, 'multi-person'):
            validate_group_proposal(proposal_for(windows), [], windows)

    async def test_one_group_is_enough_to_learn_transferable_habits(self):
        seed(self.store)
        windows = group_batch(self.store)
        scope, habits = validate_group_proposal(proposal_for(windows), [], windows)
        self.assertEqual(scope, '*')
        self.assertNotIn(GROUP, compose('base', habits))
        seed(self.store, OTHER, 100)
        windows = group_batch(self.store)
        scope, _ = validate_group_proposal(proposal_for(windows, '*'), [], windows)
        self.assertEqual(scope, '*')

    async def test_false_or_cross_group_message_evidence_is_rejected(self):
        seed(self.store)
        windows = group_batch(self.store)
        p = proposal_for(windows)
        p['habits'][0]['examples'][0]['message_id'] = 99999
        with self.assertRaises(ValueError):
            validate_group_proposal(p, [], windows)

    async def test_window_ids_are_derived_from_verified_message_citations(self):
        seed(self.store)
        windows = group_batch(self.store)
        p = proposal_for(windows)
        del p['habits'][0]['evidence_ids']
        _, habits = validate_group_proposal(p, [], windows)
        self.assertEqual(habits[0]['evidence_ids'], sorted(e['window_id'] for e in p['habits'][0]['examples']))

    async def test_open_window_not_used_and_long_flood_not_split_by_message_limit(self):
        now = time.time()
        rows = [{'id': i + 1, 'session': GROUP, 'sender': str(i % 3), 'created': now - 90 + i,
                 'content': 'repeat', 'role': 'user'} for i in range(40)]
        self.assertEqual(episodes(rows, now), [])
        completed = episodes(rows, now + 200)
        self.assertEqual(len(completed), 1)
        self.assertEqual(len(completed[0]['messages']), 16)
        self.assertEqual(completed[0]['end_id'], 40)

    async def test_quiet_groups_contribute_to_one_shared_batch(self):
        seed(self.store)
        seed(self.store, OTHER, 100)
        with self.store.db:
            self.store.db.execute('DELETE FROM group_messages WHERE session=? AND id>6', (GROUP,))
            self.store.db.execute('DELETE FROM group_messages WHERE session=? AND id>27', (OTHER,))
        windows = group_batch(self.store)
        self.assertEqual(len(windows), 5)
        self.assertEqual({w['session'] for w in windows}, {GROUP, OTHER})

    async def test_group_specific_proposal_is_not_the_learning_goal(self):
        seed(self.store)
        windows = group_batch(self.store)
        with self.assertRaisesRegex(ValueError, 'shared scope'):
            validate_group_proposal(proposal_for(windows, GROUP), [], windows)

    async def test_group_only_iteration_holds_out_real_replies_and_versions_scope(self):
        seed(self.store)
        live = '妹妹人格'
        calls = []
        async def read(): return live
        async def write(value):
            nonlocal live
            live = value
        async def generate(system, user, judge=False):
            value = json.loads(user) if system in (GROUP_PLANNER, GROUP_REVIEWER, JUDGE) else None
            calls.append((system, user))
            if system == GROUP_PLANNER:
                return json.dumps(proposal_for(value['group_windows']))
            if system == GROUP_REVIEWER:
                return json.dumps({'approve': True, 'core_preserved': True, 'evidence_supported': True})
            if system == JUDGE:
                return json.dumps({'results': [{'case_id': c['case_id'], 'x': 5 if c['x'] == 'candidate' else 3,
                                               'y': 5 if c['y'] == 'candidate' else 3, 'x_core_ok': True, 'y_core_ok': True}
                                              for c in value['cases']]})
            return 'candidate' if BEGIN in system else 'baseline'
        engine = EvolutionEngine(self.store, generate, read, write)
        await engine.initialize()
        self.assertTrue((await engine.evolve(force=True)).startswith('activated:'))
        self.assertEqual(len(self.store.pending_turns()), 0)
        active = self.store.revision()
        report = active['report']
        self.assertFalse(set(report['held_out_ids']) & set(report['evidence_ids']))
        planner = json.loads(next(u for s, u in calls if s == GROUP_PLANNER))
        self.assertEqual(len(planner['group_windows']), 4)
        references = [r['human_reference'] for r in report['replay'] if 'human_reference' in r]
        self.assertEqual(len(references), 2)
        for s, u in calls:
            if s != JUDGE:
                self.assertFalse(any(reference in u for reference in references))
        self.assertIn(active['habits'][0]['text'], live)
        self.assertNotIn('仅限会话', live)
        self.assertEqual(group_batch(self.store), [])
        await engine.rollback()
        self.assertEqual(live, '妹妹人格')

    async def test_direct_learning_can_retain_observed_human_habits(self):
        seed(self.store)
        _, habits = validate_group_proposal(proposal_for(group_batch(self.store)), [], group_batch(self.store))
        live = 'base'
        async def read(): return live
        async def write(v):
            nonlocal live
            live = v
        model = FakeModels()
        async def generate(system, user, judge=False):
            output = await model(system, user, judge)
            if system == PLANNER:
                proposal = json.loads(output)
                proposal['habits'] += json.loads(user)['previous_habits']
                return json.dumps(proposal)
            if system not in (REVIEWER, JUDGE):
                return 'candidate' if '闲聊接住当前意思就停' in system else 'baseline'
            return output
        engine = EvolutionEngine(self.store, generate, read, write, minimum_turns=10)
        await engine.initialize()
        await engine._activate(compose('base', habits), habits, {}, self.store.revision())
        self.store.put(group_source_status='disabled')
        for i in range(10):
            self.store.record(str(i), OTHER, 'a', 'input', 'reply')
        self.assertTrue((await engine.evolve(force=True)).startswith('activated:'))
        self.assertIn(habits[0]['text'], [h['text'] for h in self.store.revision()['habits']])
        planner = json.loads(next(u for s, u, _ in model.calls if s == PLANNER))
        self.assertEqual(planner['previous_habits'][0]['text'], habits[0]['text'])

    async def test_two_group_batch_checks_target_scope_on_core_cases_and_can_reject(self):
        seed(self.store)
        seed(self.store, OTHER, 100)
        live = 'base'
        writes = []
        model = FakeModels()
        model.mode = 'regress'
        async def generate(system, user, judge=False):
            if system == GROUP_PLANNER:
                return json.dumps(proposal_for(json.loads(user)['group_windows']))
            if system == GROUP_REVIEWER:
                return json.dumps({'approve': True, 'core_preserved': True, 'evidence_supported': True})
            return await model(system, user, judge)
        async def read(): return live
        async def write(value): writes.append(value)
        engine = EvolutionEngine(self.store, generate, read, write)
        await engine.initialize()
        self.assertEqual(await engine.evolve(force=True), 'rejected_replay')
        self.assertFalse(writes)
        report = self.store.revision(self.store.history()[0]['id'])['report']
        self.assertEqual(len(report['held_out_ids']), 2)
        self.assertEqual(len(report['replay']), 6)
        help_case = next(c for c in report['replay'] if c['case_id'] == 'help')
        self.assertIn('candidate', (help_case['x'], help_case['y']))


if __name__ == '__main__':
    unittest.main()
