import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugins'))
from astrbot_plugin_persona_rsi.engine import EvolutionEngine
from astrbot_plugin_persona_rsi.policy import JUDGE, accept_scores, score_summary, compose
from astrbot_plugin_persona_rsi.storage import Store
from astrbot_plugin_persona_rsi.trial import TRIAL_REVIEWER
from astrbot_plugin_persona_rsi.unified import UNIFIED_PLANNER
from test_persona_rsi import FakeModels


def scores(gains):
    rows, mapping = [], {}
    for i, gain in enumerate(gains):
        cid, candidate = str(i), ('x' if i % 2 == 0 else 'y')
        baseline = 'y' if candidate == 'x' else 'x'
        base = 3 if gain <= 2 else 1
        rows.append({'case_id': cid, candidate: base + gain, baseline: base,
                     'x_core_ok': True, 'y_core_ok': True})
        mapping[cid] = candidate == 'x'
    return {'results': rows}, mapping


class TradeoffTests(unittest.TestCase):
    def test_two_mild_regressions_can_pass_with_overall_gain(self):
        judgement, mapping = scores([2, 2, 2, 1, 0, 0, 0, -1, -1])
        report = score_summary(judgement, mapping)
        self.assertTrue(report['accepted'])
        self.assertEqual(report['mild_regressions'], 2)
        self.assertAlmostEqual(report['mean_gain'], 5 / 9)

    def test_gain_boundary_and_minimum_two_improvements(self):
        self.assertTrue(accept_scores(*scores([1, 1, 0, 0, 0, 0, 0, 0])))
        self.assertFalse(accept_scores(*scores([1, 1, 0, 0, 0, 0, 0, 0, 0])))
        self.assertFalse(accept_scores(*scores([2, 0, 0, 0])))

    def test_severe_or_widespread_regressions_still_fail(self):
        self.assertFalse(accept_scores(*scores([2, 2, 2, 0, 0, 0, 0, 0, -2])))
        self.assertFalse(accept_scores(*scores([2, 2, 2, 2, 0, 0, -1, -1, -1])))

    def test_material_core_failure_cannot_be_offset(self):
        judgement, mapping = scores([2] * 9)
        judgement['results'][1]['y_core_ok'] = False
        self.assertFalse(accept_scores(judgement, mapping))


class TrialTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.live = '妹妹人格，亲近护短。'
        self.original = self.live
        self.model = FakeModels()
        self.decision = 'keep'
        self.trial_inputs = []
        self.bad_evidence = False
        self.engine = EvolutionEngine(self.store, self.generate, self.read, self.write, minimum_turns=10)
        await self.engine.initialize()
        for i in range(10):
            self.store.record('initial' + str(i), 'qq:GroupMessage:a', 'human', '说句话', '原来的回复')

    async def asyncTearDown(self):
        self.store.close()
        self.temp.cleanup()

    async def read(self): return self.live
    async def write(self, value): self.live = value

    async def generate(self, system, user, judge=False):
        if system == TRIAL_REVIEWER:
            value = json.loads(user)
            self.trial_inputs.append(value)
            ids = [t['id'] for t in value['turns'][:2]]
            if self.bad_evidence:
                ids = [1, 2]
            return json.dumps({'decision': self.decision, 'reason': '两次实际互动支持这个判断。', 'evidence_ids': ids})
        return await self.model(system, user, judge)

    async def start_trial(self):
        result = await self.engine.evolve(force=True)
        self.assertTrue(result.startswith('activated:'), result)
        return self.store.get('active')

    def add_trial_turns(self, revision, count=20, prefix='trial'):
        for i in range(count):
            self.store.record(prefix + str(i), 'qq:GroupMessage:a', 'human', '真实互动' + str(i),
                              '新版接话' + str(i), {'revision': revision})

    async def test_only_actual_trial_version_counts_and_keep_is_persisted(self):
        revision = await self.start_trial()
        self.add_trial_turns(1, 20, 'old')
        self.store.record('untraced', 'qq:GroupMessage:a', 'human', '旧记录', '旧回答')
        self.assertEqual(await self.engine.evolve(force=True), 'trial_collecting:0/20')
        self.add_trial_turns(revision)
        self.assertEqual(await self.engine.evolve(force=True), 'trial_kept')
        self.assertEqual(self.store.get('trial')['state'], 'kept')
        self.assertEqual(self.store.get('active'), revision)
        self.assertEqual(len(self.trial_inputs[0]['turns']), 18)
        self.assertTrue(all(t['trace']['revision'] == revision for t in self.trial_inputs[0]['turns']))
        self.assertNotIn('真实互动19', json.dumps(self.trial_inputs, ensure_ascii=False))
        self.assertEqual(self.store.revision()['report']['trial_reviews'][0]['decision'], 'keep')

    async def test_observe_moves_cursor_and_does_not_reuse_same_samples(self):
        revision = await self.start_trial()
        self.add_trial_turns(revision)
        self.decision = 'observe'
        self.assertEqual(await self.engine.evolve(force=True), 'trial_observing')
        self.assertEqual(await self.engine.evolve(force=True), 'trial_collecting:0/20')
        self.assertEqual(self.store.get('active'), revision)

    async def test_trial_feedback_can_restore_parent_without_stopping_learning(self):
        revision = await self.start_trial()
        self.add_trial_turns(revision)
        self.decision = 'rollback'
        self.assertTrue((await self.engine.evolve(force=True)).startswith('activated:'))
        self.assertEqual(self.live, self.original)
        self.assertIsNone(self.store.get('trial'))
        self.assertFalse(self.store.get('paused'))
        self.assertEqual(self.store.revision(revision)['report']['trial_reviews'][0]['decision'], 'rollback')

    async def test_adjustment_uses_feedback_and_keeps_holdout_unseen(self):
        revision = await self.start_trial()
        self.add_trial_turns(revision)
        self.decision = 'adjust'
        result = await self.engine.evolve(force=True)
        # Identical simulated replies do not demonstrate improvement in this test.
        self.assertEqual(result, 'rejected_replay')
        planner = json.loads([u for s, u, _ in self.model.calls if s == UNIFIED_PLANNER][-1])
        self.assertEqual(planner['trial_feedback']['decision'], 'adjust')
        self.assertEqual(len(planner['turns']), 18)
        self.assertNotIn('真实互动19', json.dumps(planner, ensure_ascii=False))
        self.assertEqual(self.store.get('trial')['state'], 'observing')
        self.assertEqual(self.store.get('active'), revision)

    async def test_forged_old_version_feedback_cannot_keep_or_rollback(self):
        revision = await self.start_trial()
        self.add_trial_turns(revision)
        self.bad_evidence = True
        self.decision = 'rollback'
        with self.assertRaisesRegex(ValueError, 'this version'):
            await self.engine.evolve(force=True)
        self.assertEqual(self.store.get('active'), revision)
        self.assertEqual(self.store.get('trial')['after_turn_id'], 10)

    async def test_trial_survives_restart_and_manual_rollback_clears_it(self):
        revision = await self.start_trial()
        self.add_trial_turns(revision, 5)
        self.store.close()
        self.store = Store(self.temp.name)
        self.engine = EvolutionEngine(self.store, self.generate, self.read, self.write)
        await self.engine.initialize()
        self.assertEqual(await self.engine.evolve(force=True), 'trial_collecting:5/20')
        await self.engine.rollback()
        self.assertIsNone(self.store.get('trial'))
        self.assertTrue(self.store.get('paused'))

    async def test_old_mild_regression_candidate_is_rejudged_and_trialed_once(self):
        habits = [{'dimension': 'brevity', 'text': '接住意思就停。', 'evidence_ids': [1, 2]}]
        judgement, mapping = scores([2, 2, 2, 1, 0, 0, 0, -1, -1])
        replay = [{'case_id': str(i), 'input': '接句话',
                   'x': 'candidate' if mapping[str(i)] else 'baseline',
                   'y': 'baseline' if mapping[str(i)] else 'candidate'} for i in range(9)]
        report = {'review': {'approve': True, 'core_preserved': True, 'evidence_supported': True},
                  'functional_tests': {'passed': True}, 'judgement': judgement,
                  'candidate_is_x': mapping, 'replay': replay, 'targets': ['persona']}
        rid = self.store.add_revision(1, 'rejected_replay', compose(self.live, habits), habits, report)
        self.store.put(reassessment_pending=True)
        self.assertTrue((await self.engine.evolve(force=True)).startswith('activated:'))
        self.assertEqual(self.store.revision()['report']['reassesses'], rid)
        self.assertEqual(self.store.get('trial')['state'], 'observing')
        self.assertFalse(self.store.get('reassessment_pending'))
        self.assertEqual([s for s, _, _ in self.model.calls], [JUDGE])
        self.assertEqual(self.store.revision(rid)['status'], 'rejected_replay')
        self.assertEqual(self.store.get('watermark'), 0)
        self.assertEqual(await self.engine.evolve(force=True), 'trial_collecting:0/20')

    async def test_recovery_of_interrupted_activation_preserves_trial_marker(self):
        habits = [{'dimension': 'brevity', 'text': '接住意思就停。', 'evidence_ids': [1, 2]}]
        self.live = compose(self.live, habits)
        report = {'trial': {'state': 'observing', 'after_turn_id': 10, 'required_turns': 20}}
        rid = self.store.add_revision(1, 'applying', self.live, habits, report)
        self.store.put(applying=rid)
        await self.engine.initialize()
        self.assertEqual(self.store.get('trial')['revision'], rid)
        self.assertEqual(self.store.get('trial')['parent'], 1)


if __name__ == '__main__':
    unittest.main()
