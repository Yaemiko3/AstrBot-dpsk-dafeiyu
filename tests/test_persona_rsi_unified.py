import asyncio
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugins'))
from astrbot_plugin_persona_rsi.engine import EvolutionEngine
from astrbot_plugin_persona_rsi.storage import Store
from astrbot_plugin_persona_rsi.runtime import BehaviorRuntime, default_bundle, append_context
from astrbot_plugin_persona_rsi.unified import UNIFIED_PLANNER, UNIFIED_REVIEWER, validate_change
from astrbot_plugin_persona_rsi.policy import JUDGE, BEGIN


class UnifiedTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.live = '固定妹妹人格'
        self.writes = []
        self.proposal = None
        self.calls = []
        self.engine = EvolutionEngine(self.store, self.generate, self.read, self.write, minimum_turns=10)
        await self.engine.initialize()
        for i in range(10):
            trace = {'request': {'id': 'current', 'sender': 'a', 'created': 100, 'now': 100},
                     'messages': [{'id': 'useful', 'sender': 'b', 'created': 98, 'text': 'USEFUL 角色名字'},
                                  {'id': 'noise1', 'sender': 'b', 'created': 98.5, 'text': '吃了吗'},
                                  {'id': 'noise2', 'sender': 'c', 'created': 99, 'text': '猫猫'}]}
            self.store.record(str(i), 'qq:GroupMessage:a', 'human', '几岁了', '不知道', trace)

    async def asyncTearDown(self):
        self.store.close()
        self.temp.cleanup()

    async def read(self): return self.live

    async def write(self, value):
        self.live = value
        self.writes.append(value)

    async def generate(self, system, user, judge=False):
        self.calls.append((system, user))
        if system == UNIFIED_PLANNER:
            return json.dumps(self.proposal, ensure_ascii=False)
        if system == UNIFIED_REVIEWER:
            return json.dumps({'approve': True, 'core_preserved': True, 'evidence_supported': True})
        if system == JUDGE:
            return json.dumps({'results': [{'case_id': c['case_id'], 'x': 5 if c['x'] == 'candidate' else 2,
                                           'y': 5 if c['y'] == 'candidate' else 2,
                                           'x_core_ok': True, 'y_core_ok': True} for c in json.loads(user)['cases']]})
        return 'candidate' if BEGIN in system or 'USEFUL' in user else 'baseline'

    def draft(self, changes):
        return {'decision': 'propose', 'reason': '两次指代缺上下文，需要选入前面的角色消息。',
                'evidence': {'turn_ids': [1, 2]}, 'changes': changes}

    async def test_review_feedback_repairs_once_with_same_training_and_full_review(self):
        self.proposal = self.draft({'persona': {'habits': [
            {'dimension': 'brevity', 'text': '闲聊接住意思就停。', 'evidence_ids': [1, 2]}]}})
        reviews, drafts = [], []

        async def generate(system, user, judge=False):
            if system == UNIFIED_PLANNER:
                value = json.loads(user)
                drafts.append(value)
                if 'correction' in value:
                    self.assertEqual(value['correction']['feedback']['reason'], '去掉无关的身份论断')
                    self.proposal['reason'] = '只根据两次闲聊中追加解释的问题改进。'
            if system == UNIFIED_REVIEWER:
                reviews.append(json.loads(user))
                if len(reviews) == 1:
                    return json.dumps({'approve': False, 'core_preserved': True,
                                       'evidence_supported': False, 'reason': '去掉无关的身份论断'})
            return await self.generate(system, user, judge)

        self.engine.generate = generate
        self.assertTrue((await self.engine.evolve(force=True)).startswith('activated:'))
        self.assertEqual(len(drafts), 2)
        self.assertEqual(len(reviews), 2)
        self.assertEqual(drafts[0]['turns'], drafts[1]['turns'])
        self.assertEqual({t['id'] for t in drafts[1]['turns']}, set(range(1, 9)))
        self.assertNotIn('held_out', drafts[1])
        report = self.store.revision()['report']
        self.assertEqual(report['attempt'], 2)
        rejected = self.store.revision(report['correction']['retry_of'])
        self.assertEqual(rejected['status'], 'rejected_review')
        self.assertEqual(rejected['report']['attempt'], 1)
        self.assertEqual(len(self.writes), 1)
        self.assertEqual(self.store.get('watermark'), 10)

    async def test_repeated_review_failure_stops_without_replay_or_write(self):
        self.proposal = self.draft({'parameters': {'context_message_limit': 3}})
        reviews = []

        async def generate(system, user, judge=False):
            if system == UNIFIED_REVIEWER:
                reviews.append(json.loads(user))
                return json.dumps({'approve': False, 'core_preserved': True,
                                   'evidence_supported': False, 'reason': '证据仍不足'})
            return await self.generate(system, user, judge)

        self.engine.generate = generate
        self.assertEqual(await self.engine.evolve(force=True), 'rejected_review')
        self.assertEqual(len(reviews), 2)
        self.assertEqual(sum(s == UNIFIED_PLANNER for s, _ in self.calls), 2)
        self.assertFalse(any(s == JUDGE for s, _ in self.calls))
        self.assertFalse(self.writes)
        self.assertEqual(await self.engine.evolve(), 'cooldown')

    async def test_invalid_evidence_can_be_corrected_but_still_requires_review(self):
        self.proposal = self.draft({'parameters': {'context_message_limit': 3}})
        self.proposal['evidence']['turn_ids'] = [1, 9999]

        async def generate(system, user, judge=False):
            if system == UNIFIED_PLANNER and 'correction' in json.loads(user):
                self.proposal['evidence']['turn_ids'] = [1, 2]
            return await self.generate(system, user, judge)

        self.engine.generate = generate
        self.assertTrue((await self.engine.evolve(force=True)).startswith('activated:'))
        report = self.store.revision()['report']
        self.assertEqual(report['correction']['feedback']['stage'], 'evidence')
        self.assertEqual(sum(s == UNIFIED_REVIEWER for s, _ in self.calls), 1)

    async def test_pause_on_rejection_prevents_correction(self):
        self.proposal = self.draft({'parameters': {'context_message_limit': 3}})

        async def generate(system, user, judge=False):
            if system == UNIFIED_REVIEWER:
                self.store.put(paused=True)
                return json.dumps({'approve': False, 'reason': '停止'})
            return await self.generate(system, user, judge)

        self.engine.generate = generate
        self.assertEqual(await self.engine.evolve(force=True), 'paused_before_apply')
        self.assertEqual(sum(s == UNIFIED_PLANNER for s, _ in self.calls), 1)
        self.assertFalse(self.writes)

    async def test_style_rubrics_reach_judge_only_and_do_not_replace_help_case(self):
        self.proposal = self.draft({'persona': {'habits': [
            {'dimension': 'brevity', 'text': '闲聊接住意思就停。', 'evidence_ids': [1, 2]}]}})
        await self.engine.evolve(force=True)
        judged = json.loads(next(u for s, u in self.calls if s == JUDGE))['cases']
        cases = {c['case_id']: c for c in judged}
        self.assertIn('help', cases)
        for cid in ('casual_complaint', 'joke_stop', 'natural_ending'):
            focus = cases[cid]['evaluation_focus']
            self.assertTrue(focus)
            self.assertFalse(any(focus in u for s, u in self.calls if s != JUDGE))

    async def test_parameters_only_can_change_real_inputs_without_persona_edit(self):
        self.proposal = self.draft({'parameters': {'context_message_limit': 3}})
        result = await self.engine.evolve(force=True)
        self.assertTrue(result.startswith('activated:'), result)
        self.assertEqual(self.live, '固定妹妹人格')
        active = self.store.revision()
        self.assertEqual(active['habits'], [])
        self.assertEqual(active['bundle']['parameters']['context_message_limit'], 3)
        self.assertEqual(active['report']['targets'], ['parameters'])
        self.assertEqual(json.loads((Path(self.temp.name) / 'active_runtime.json').read_text())['context_message_limit'], 3)
        self.assertTrue(Path(active['report']['workspace'], 'evaluation.json').is_file())

    async def test_code_only_is_executed_and_can_change_context_selection(self):
        initial = default_bundle()
        initial['parameters']['context_message_limit'] = 2
        await self.engine._activate(self.live, [], {'kind': 'setup'}, self.store.revision(), bundle=initial)
        changed = initial['code'].replace('eligible[-limit:]', 'eligible[:limit]')
        self.proposal = self.draft({'code': {'path': 'behavior.py', 'source': changed}})
        result = await self.engine.evolve(force=True)
        self.assertTrue(result.startswith('activated:'), result)
        self.assertEqual(self.live, '固定妹妹人格')
        active = self.store.revision()
        self.assertEqual(active['bundle']['code'], changed)
        self.assertEqual(active['report']['targets'], ['code'])
        self.assertTrue(active['report']['functional_tests']['passed'])

    async def test_combined_change_rolls_back_as_one_version(self):
        initial = self.store.revision()
        changed = initial['bundle']['code'].replace('eligible[-limit:]', 'eligible[:limit]')
        self.proposal = self.draft({'persona': {'habits': [{'dimension': 'brevity', 'text': '接住意思就停。', 'evidence_ids': [1, 2]}]},
                                    'parameters': {'context_message_limit': 3},
                                    'code': {'path': 'behavior.py', 'source': changed}})
        self.assertTrue((await self.engine.evolve(force=True)).startswith('activated:'))
        adopted = self.store.revision()
        self.assertEqual(set(adopted['report']['targets']), {'persona', 'parameters', 'code'})
        self.assertIn(BEGIN, self.live)
        await self.engine.rollback()
        self.assertEqual(self.live, initial['prompt'])
        self.assertEqual(self.store.revision()['bundle'], initial['bundle'])
        self.assertEqual((Path(self.temp.name) / 'active_behavior.py').read_text(encoding='utf-8'), initial['bundle']['code'])

    async def test_invalid_runtime_code_fails_tests_before_review_or_write(self):
        code = default_bundle()['code'].replace("'context_ids': [m['id'] for m in selected]", "'context_ids': ['nonexistent']")
        self.proposal = self.draft({'code': {'path': 'behavior.py', 'source': code}})
        self.assertEqual(await self.engine.evolve(force=True), 'rejected_tests')
        self.assertFalse(self.writes)
        self.assertFalse(any(s == UNIFIED_REVIEWER for s, _ in self.calls))

    async def test_unsupported_file_and_forged_evidence_do_not_write(self):
        active = self.store.revision()
        for changes in ({'code': {'path': '../main.py', 'source': default_bundle()['code']}},
                        {'parameters': {'unknown': 123}}):
            with self.assertRaises(ValueError):
                validate_change(self.draft(changes), active, 'direct', self.store.pending_turns())
        proposal = self.draft({'parameters': {'context_message_limit': 3}})
        proposal['evidence']['turn_ids'] = [1, 9999]
        with self.assertRaises(ValueError):
            validate_change(proposal, active, 'direct', self.store.pending_turns())

    async def test_restart_completes_pending_parameter_only_version(self):
        active = self.store.revision()
        candidate = copy.deepcopy(active['bundle'])
        candidate['parameters']['context_message_limit'] = 3
        rid = self.store.add_revision(active['id'], 'applying', active['prompt'], [], {}, bundle=candidate)
        self.store.put(applying=rid)
        await self.engine.initialize()
        self.assertEqual(self.store.get('active'), rid)
        self.assertEqual(self.store.revision()['bundle'], candidate)

    async def test_live_runtime_fault_restores_whole_version_once(self):
        self.proposal = self.draft({'parameters': {'context_message_limit': 3}})
        await self.engine.evolve(force=True)
        failed_id = self.store.get('active')
        await self.engine.runtime_failure(failed_id, 'unexpected live shape')
        restored = self.store.get('active')
        self.assertTrue(self.store.get('paused'))
        self.assertEqual(self.store.revision()['bundle'], default_bundle())
        await self.engine.runtime_failure(failed_id, 'late duplicate fault')
        self.assertEqual(self.store.get('active'), restored)

    async def test_behavior_worker_has_timeout(self):
        bundle = default_bundle()
        bundle['code'] = '''def plan_request(messages, request, settings):
    total = 0
    for a in range(1000000):
        for b in range(1000000):
            total += a + b
    return {'wait_ms': 0, 'context_ids': [], 'merge_ids': []}
def plan_response(text, settings):
    return {'delay_ms': 0}
'''
        with self.assertRaises(asyncio.TimeoutError):
            await self.engine.runtime.request(bundle, [], {'id': 'a', 'sender': 'a', 'created': 1, 'now': 1})


if __name__ == '__main__':
    unittest.main()
