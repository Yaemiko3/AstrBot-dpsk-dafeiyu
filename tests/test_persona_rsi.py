import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugins'))
from astrbot_plugin_persona_rsi.engine import EvolutionEngine
from astrbot_plugin_persona_rsi.policy import BEGIN, CORE, PLANNER, REVIEWER, JUDGE, accept_scores, compose, validate_proposal, parse_json
from astrbot_plugin_persona_rsi.storage import Store
from astrbot_plugin_persona_rsi.unified import UNIFIED_PLANNER as PLANNER, UNIFIED_REVIEWER as REVIEWER


class FakeModels:
    def __init__(self):
        self.mode = 'accept'
        self.calls = []
        self.review_action = None

    async def __call__(self, system, user, judge=False):
        self.calls.append((system, user, judge))
        if system == PLANNER:
            value = json.loads(user)
            if self.mode == 'invalid':
                return 'not json'
            if self.mode == 'no_change':
                return json.dumps({'decision': 'no_change', 'reason': 'Insufficient feedback'})
            ids = [t['id'] for t in value['turns'][:2]]
            return json.dumps({'decision': 'propose', 'reason': 'Repeated explanatory endings in two exchanges',
                               'habits': [{'dimension': 'brevity', 'text': '闲聊接住当前意思就停，不追加解释自己的评价。', 'evidence_ids': ids}]})
        if system == REVIEWER:
            if self.review_action:
                await self.review_action()
            return json.dumps({'approve': self.mode != 'reject', 'core_preserved': True, 'evidence_supported': True, 'reason': 'Supported'})
        if system == JUDGE:
            rows = []
            for case in json.loads(user)['cases']:
                x = 5 if case['x'] == 'candidate' else 3
                y = 5 if case['y'] == 'candidate' else 3
                if self.mode == 'regress' and case['case_id'] == 'help':
                    x, y = 6 - x, 6 - y
                rows.append({'case_id': case['case_id'], 'x': x, 'y': y, 'x_core_ok': True, 'y_core_ok': True})
            return json.dumps({'results': rows})
        return 'candidate' if BEGIN in system else 'baseline'


class EngineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.live = '妹妹人格。被 @ 必回。普通闲聊每 3～5 轮一张表情。'
        self.original = self.live
        self.models = FakeModels()
        self.writes = []
        self.engine = EvolutionEngine(self.store, self.models, self.read, self.write, minimum_turns=10)
        await self.engine.initialize()
        for index in range(10):
            self.store.record(str(index), 'group-a', 'user-a', '群友的问题' + str(index), '机器人的回复' + str(index))

    async def asyncTearDown(self):
        self.store.close()
        self.temp.cleanup()

    async def read(self):
        return self.live

    async def write(self, prompt):
        self.live = prompt
        self.writes.append(prompt)

    async def test_activation_persists_real_persona_and_bounds(self):
        result = await self.engine.evolve(force=True)
        self.assertTrue(result.startswith('activated:'))
        self.assertTrue(self.live.startswith(self.original))
        self.assertIn(CORE, self.live)
        self.assertEqual(self.live.count(BEGIN), 1)
        self.assertEqual(self.store.revision()['prompt'], self.live)
        self.assertEqual((Path(self.temp.name) / 'active_persona.md').read_text(encoding='utf-8'), self.live)
        report = self.store.revision()['report']
        self.assertFalse(set(report['evidence_ids']) & set(report['held_out_ids']))
        self.assertEqual(len(report['replay']), 9)
        self.assertEqual(await self.engine.evolve(force=True), 'trial_collecting:0/20')

    async def test_reviewer_rejection_never_writes(self):
        self.models.mode = 'reject'
        self.assertEqual(await self.engine.evolve(force=True), 'rejected_review')
        self.assertFalse(self.writes)

    async def test_custom_core_flows_through_review_replay_and_persona(self):
        core = '固定核心：GPT 龙娘；沿用目标人格的身份和关系。'
        self.engine = EvolutionEngine(self.store, self.models, self.read, self.write,
                                      minimum_turns=10, core_rules=core)
        result = await self.engine.evolve(force=True)
        self.assertTrue(result.startswith('activated:'))
        self.assertIn(core, self.live)
        self.assertNotIn(CORE, self.live)
        for system in (PLANNER, REVIEWER, JUDGE):
            calls = [json.loads(user) for prompt, user, _ in self.models.calls if prompt == system]
            self.assertTrue(calls)
            self.assertTrue(all(call['core'] == core for call in calls))

    async def test_severe_regression_blocks_activation(self):
        self.models.mode = 'regress'
        self.assertEqual(await self.engine.evolve(force=True), 'rejected_replay')
        self.assertFalse(self.writes)

    async def test_no_change_is_a_valid_recorded_outcome(self):
        self.models.mode = 'no_change'
        self.assertEqual(await self.engine.evolve(force=True), 'no_change')
        self.assertEqual(self.store.history()[0]['status'], 'no_change')
        self.assertFalse(self.writes)

    async def test_invalid_model_output_preserves_persona(self):
        self.models.mode = 'invalid'
        with self.assertRaises(ValueError):
            await self.engine.evolve(force=True)
        self.assertEqual(self.live, self.original)
        self.assertTrue(self.store.get('last_result').startswith('failed:'))
        self.assertEqual(len(self.models.calls), 2)
        self.assertFalse(self.store.get('last_json_error')['recovered'])

    async def test_json_format_retry_keeps_stage_and_evidence_then_completes_review(self):
        drafts = []
        async def generate(system, user, judge=False):
            if system == PLANNER:
                value = json.loads(user)
                drafts.append(value)
                if 'format_feedback' not in value:
                    return '{"decision":"propose","reason":"unescaped "quote""}'
            return await self.models(system, user, judge)
        self.engine.generate = generate
        result = await self.engine.evolve(force=True)
        self.assertTrue(result.startswith('activated:'))
        self.assertEqual(len(drafts), 2)
        self.assertEqual(drafts[0]['turns'], drafts[1]['turns'])
        self.assertIn('previous_output', drafts[1]['format_feedback'])
        self.assertTrue(self.store.get('last_json_error')['recovered'])
        self.assertTrue(any(system == REVIEWER for system, _, _ in self.models.calls))
        self.assertTrue(any(system == JUDGE for system, _, _ in self.models.calls))

    async def test_external_edit_during_review_is_not_overwritten(self):
        async def edit(): self.live = 'new manual persona'
        self.models.review_action = edit
        self.assertEqual(await self.engine.evolve(force=True), 'external_edit')
        self.assertEqual(self.live, 'new manual persona')
        self.assertFalse(self.writes)

    async def test_pause_during_review_prevents_commit(self):
        async def pause(): self.store.put(paused=True)
        self.models.review_action = pause
        self.assertEqual(await self.engine.evolve(force=True), 'paused_before_apply')
        self.assertFalse(self.writes)

    async def test_rollback_restores_and_pauses(self):
        await self.engine.evolve(force=True)
        self.assertTrue((await self.engine.rollback()).startswith('activated:'))
        self.assertEqual(self.live, self.original)
        self.assertTrue(self.store.get('paused'))
        self.assertEqual(await self.engine.evolve(force=True), 'paused')

    async def test_recovery_after_write_before_commit(self):
        habits = [{'dimension': 'brevity', 'text': '直接接话。', 'evidence_ids': [1, 2]}]
        prompt = compose(self.original, habits)
        rid = self.store.add_revision(self.store.get('active'), 'applying', prompt, habits, {})
        self.store.put(applying=rid)
        self.live = prompt
        await self.engine.initialize()
        self.assertEqual(self.store.get('active'), rid)
        self.assertIsNone(self.store.get('applying'))

    async def test_recovery_when_write_never_happened(self):
        rid = self.store.add_revision(self.store.get('active'), 'applying', 'not written', [], {})
        self.store.put(applying=rid)
        await self.engine.initialize()
        self.assertEqual(self.store.revision(rid)['status'], 'interrupted')
        self.assertEqual(self.live, self.original)

    async def test_adopt_rollback_restores_base(self):
        self.live = 'new manually edited base'
        await self.engine.adopt()
        await self.engine.rollback()
        self.assertEqual(self.store.get('base'), self.original)
        self.assertEqual(self.live, self.original)

    async def test_duplicate_event_is_not_double_counted(self):
        self.assertFalse(self.store.record('0', 'group-a', 'user-a', 'duplicate', 'duplicate'))
        self.assertEqual(len(self.store.pending_turns()), 10)

    async def test_concurrent_cycle_is_skipped(self):
        async with self.engine.lock:
            self.assertEqual(await self.engine.evolve(force=True), 'busy')

    async def test_cooldown_survives_store_reopen(self):
        self.models.mode = 'no_change'
        await self.engine.evolve(force=True)
        self.store.close()
        self.store = Store(self.temp.name)
        engine = EvolutionEngine(self.store, self.models, self.read, self.write, minimum_turns=10)
        self.assertEqual(await engine.evolve(), 'cooldown')


class PolicyTests(unittest.TestCase):
    def test_json_wrappers_and_bom_preserve_full_object(self):
        for raw in ['\ufeff{"ok":true}', '```json\n{"ok":true}\n```', '```JSON {"ok":true}```']:
            self.assertEqual(parse_json(raw), {'ok': True})
        for raw in ['{"ok":true', '{"ok":true}\n{"ok":false}', '```json\n{"ok":true}', '[]']:
            with self.assertRaises(ValueError):
                parse_json(raw)

    def test_evidence_feedback_distinguishes_excess_count_from_wrong_id_type(self):
        turns = [{'id': i} for i in range(1, 20)]
        proposal = {'decision': 'propose', 'reason': 'test', 'habits': [
            {'dimension': 'brevity', 'text': '自然收尾。', 'evidence_ids': list(range(1, 13))}]}
        with self.assertRaisesRegex(ValueError, 'received 12'):
            validate_proposal(proposal, [], turns)
        proposal['habits'][0]['evidence_ids'] = ['1', '2']
        with self.assertRaisesRegex(ValueError, 'integer turns'):
            validate_proposal(proposal, [], turns)
        proposal['habits'][0]['evidence_ids'] = [1, 2]
        self.assertEqual(len(validate_proposal(proposal, [], turns)), 1)

    def test_hallucinated_and_repeated_evidence_rejected(self):
        for evidence in ([1, 999], [1, 1], [True, 2]):
            proposal = {'decision': 'propose', 'reason': 'test', 'habits': [{'dimension': 'tone', 'text': '自然说话。', 'evidence_ids': evidence}]}
            with self.assertRaises(ValueError):
                validate_proposal(proposal, [], [{'id': 1}, {'id': 2}])

    def test_missing_judgements_cannot_pass(self):
        with self.assertRaises(ValueError):
            accept_scores({'results': []}, {'a': True})

    def test_core_violation_cannot_be_offset_by_score(self):
        result = {'results': [{'case_id': 'a', 'x': 5, 'y': 1, 'x_core_ok': False, 'y_core_ok': True}]}
        self.assertFalse(accept_scores(result, {'a': True}))

    def test_duplicate_generated_block_rejected(self):
        with self.assertRaises(ValueError):
            compose('old' + BEGIN, [])


if __name__ == '__main__':
    unittest.main()
