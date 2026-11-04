"""Evidence -> candidate -> independent review -> blind replay -> version activation."""
import asyncio
import json
import random
import time
import uuid

from .policy import BEGIN, END, CORE, PLANNER, REVIEWER, JUDGE, CASES, compose, parse_json, validate_proposal, accept_scores
from .group_learning import group_batch
from .runtime import BehaviorRuntime, append_context, bundle_hash
from .unified import UNIFIED_PLANNER, UNIFIED_REVIEWER, validate_change, public_runtime
from .expression import EXPRESSION_OBSERVER, expression_proposal


class EvolutionEngine:
    def __init__(self, store, generate, read_persona, write_persona, minimum_turns=40, cooldown_hours=6):
        self.store = store
        self.generate = generate  # async (system, user, judge=False) -> text
        self.read_persona = read_persona
        self.write_persona = write_persona
        self.minimum_turns = min(60, max(10, int(minimum_turns)))
        self.cooldown = max(0.25, float(cooldown_hours)) * 3600
        self.lock = asyncio.Lock()
        self.runtime = BehaviorRuntime()

    async def initialize(self):
        current = await self.read_persona()
        if self.store.get('active') is None:
            if BEGIN in current or END in current:
                raise ValueError('Existing RSI block requires its original version database')
            rid = self.store.add_revision(None, 'active', current, [], {'kind': 'baseline'})
            self.store.put(active=rid, base=current, watermark=0, paused=False, last_attempt=0)
            self.store.export()
        applying = self.store.get('applying')
        if applying:
            pending = self.store.revision(applying)
            await self.runtime.verify(pending['bundle'])
            if current == pending['prompt']:
                self.store.activate(applying)
            elif current == self.store.revision()['prompt']:
                self.store.mark(applying, 'interrupted')
                self.store.put(applying=None)
            else:
                raise ValueError('Persona changed during interrupted activation')
        await self.runtime.verify(self.store.revision()['bundle'])
        self.store.export()
        if current != self.store.revision()['prompt']:
            self.store.put(last_result='external_edit: adopt explicitly before evolving')

    async def _json(self, system, value, judge=False):
        return parse_json(await asyncio.wait_for(self.generate(system, json.dumps(value, ensure_ascii=False), judge), timeout=60))

    async def evolve(self, force=False):
        if self.lock.locked():
            return 'busy'
        async with self.lock:
            if self.store.get('applying'):
                await self.initialize()
            if self.store.get('paused', False):
                return 'paused'
            if not force and time.time() - self.store.get('last_attempt', 0) < self.cooldown:
                return 'cooldown'
            turns = self.store.pending_turns()
            windows = group_batch(self.store)
            # Prefer human expression; after two human batches allow one ready
            # feedback batch so sustained group traffic cannot starve correction.
            use_groups = bool(windows) and (len(turns) < self.minimum_turns
                                           or self.store.get('expression_streak', 0) < 2)
            if not use_groups and len(turns) < self.minimum_turns:
                return f'collecting:{len(turns)}/{self.minimum_turns}'
            if await self.read_persona() != self.store.revision()['prompt']:
                self.store.put(last_result='external_edit')
                return 'external_edit'
            # One cycle owns this batch, including at most one correction with the
            # same training evidence. Later cycles still require fresh evidence.
            if use_groups:
                marks = self.store.get('group_watermarks', {})
                reviewed = self.store.get('group_reviewed_at', {})
                for window in windows:
                    session = window['session']
                    marks[session] = max(marks.get(session, 0), window['end_id'])
                    reviewed[session] = time.time()
                self.store.put(last_attempt=time.time(), last_source='group', group_watermarks=marks,
                               group_reviewed_at=reviewed, expression_streak=self.store.get('expression_streak', 0) + 1)
            else:
                self.store.put(last_attempt=time.time(), last_source='direct', watermark=turns[-1]['id'], expression_streak=0)
            try:
                operation = self._group_iteration(windows) if use_groups else self._iteration(turns)
                result = await asyncio.wait_for(operation, timeout=720)
            except Exception as exc:
                self.store.put(last_result='failed:' + type(exc).__name__)
                raise
            self.store.put(last_result=result)
            return result

    async def _iteration(self, turns):
        training, held_out = turns[:-2], turns[-2:]
        cases = list(CASES)
        for index, turn in enumerate(held_out):
            previous = [t for t in turns if t['session'] == turn['session'] and t['id'] < turn['id']][-1:]
            context = ''.join('前一轮群友说：' + t['user'] + '\n我当时回复：' + t['assistant'] + '\n' for t in previous)
            case = {'id': 'holdout_' + str(index), 'input': context + '当前群友说：' + turn['user']}
            trace = turn.get('trace', {})
            if trace.get('request') and isinstance(trace.get('messages'), list):
                case.update(request=trace['request'], messages=trace['messages'])
            cases.append(case)
        return await self._unified_iteration('direct', training, held_out, cases)

    async def _group_iteration(self, windows):
        training, held_out = windows[:-2], windows[-2:]
        cases = list(CASES)
        for index, window in enumerate(held_out):
            context = '\n'.join(m['speaker'] + '：' + m['text'] for m in window['messages'][:-1])
            cases.append({'id': 'group_holdout_' + str(index),
                          'input': '以下是群友之间刚发生的交流：\n' + context + '\n现在轮到大肥鱼接一句，请按自己的身份自然接话。',
                          'human_reference': window['messages'][-1]['text']})
        return await self._unified_iteration('group', training, held_out, cases)

    async def _unified_iteration(self, source, training, held_out, cases, correction=None):
        if self.store.get('paused', False):
            return 'paused_before_apply'
        active = self.store.revision()
        previous = [{k: v for k, v in h.items() if k != 'scope' and (source == 'group' or k != 'examples')} for h in active['habits']]
        if source == 'group':
            inputs = {'core': CORE, 'source': source, 'objective': 'learn_human_expression',
                      'previous_habits': previous, 'group_windows': training}
            planner = EXPRESSION_OBSERVER
        else:
            inputs = {'core': CORE, 'source': source, 'objective': 'auxiliary_feedback',
                      'base_persona': self.store.get('base'), 'previous_habits': previous,
                      **public_runtime(active['bundle']), 'turns': training}
            planner = UNIFIED_PLANNER
        if correction is not None:
            inputs['correction'] = correction
        proposal = await self._json(planner, inputs)
        observation = proposal if source == 'group' else None
        # Accept old persona-only draft files through the same executor.
        if 'habits' in proposal and 'changes' not in proposal:
            proposal = {**proposal, 'changes': {'persona': {'habits': proposal['habits']}}}
        report = {'source': source, 'proposal': proposal,
                  'evidence_ids': [t['id'] for t in training], 'held_out_ids': [t['id'] for t in held_out],
                  'evidence': training, 'attempt': 2 if correction is not None else 1}
        if correction is not None:
            report['correction'] = correction
        if observation is not None:
            report['observation'] = observation

        async def correct(revision_id, feedback):
            return await self._unified_iteration(source, training, held_out, cases, correction={
                'retry_of': revision_id, 'previous_proposal': observation if observation is not None else proposal,
                'feedback': feedback})

        try:
            if observation is not None:
                proposal = expression_proposal(observation, active)
                report['proposal'] = proposal
            change = validate_change(proposal, active, source, training)
        except (ValueError, TypeError, SyntaxError) as exc:
            report['evidence_error'] = str(exc)
            rid = self.store.add_revision(active['id'], 'rejected_evidence', active['prompt'], active['habits'], report)
            if correction is None:
                return await correct(rid, {'stage': 'evidence', 'reason': str(exc)})
            return 'rejected_evidence'
        if change is None:
            rid = self.store.add_revision(active['id'], 'no_change', active['prompt'], active['habits'], report)
            if source == 'group' and correction is None:
                return await correct(rid, {
                    'stage': 'group_observation',
                    'reason': '复核本次不修改的依据：旁观学习不要求机器人参与、被@、犯错或得到评价。'
                              '请分析真人之间如何回应抱怨、接梗、复读、收尾和控制回复长度，核对能否满足多人多场合的引用。'
                              '真人表达本身是证据，不需要证明机器人原有原则执行失败。提炼适用情境、表达动作和停止条件，交给试用对照验证。'
                              '没有可迁移模式、证据不足或没有有效改法时仍返回 no_change，说明具体缺口；不要强行修改。'})
            return 'no_change'
        habits, bundle = change['habits'], change['bundle']
        candidate = compose(self.store.get('base'), habits)
        report['targets'] = change['targets']
        workspace = self.store.directory / 'candidates' / uuid.uuid4().hex
        workspace.mkdir(parents=True)
        (workspace / 'persona.md').write_text(candidate, encoding='utf-8')
        (workspace / 'parameters.json').write_text(json.dumps(bundle['parameters'], indent=2), encoding='utf-8')
        (workspace / 'behavior.py').write_text(bundle['code'], encoding='utf-8')
        (workspace / 'proposal.json').write_text(json.dumps(proposal, ensure_ascii=False, indent=2), encoding='utf-8')
        report['workspace'] = str(workspace)
        try:
            report['functional_tests'] = await self.runtime.verify(bundle)
        except Exception as exc:
            report['functional_tests'] = {'passed': False, 'error': type(exc).__name__ + ': ' + str(exc)}
            self.store.add_revision(active['id'], 'rejected_tests', candidate, habits, report, bundle=bundle)
            return 'rejected_tests'
        review = await self._json(UNIFIED_REVIEWER, {**inputs, 'base_persona': self.store.get('base'),
                                  'proposal': proposal, 'functional_tests': report['functional_tests']}, judge=True)
        report['review'] = review
        if not all(review.get(k) is True for k in ('approve', 'core_preserved', 'evidence_supported')):
            rid = self.store.add_revision(active['id'], 'rejected_review', candidate, habits, report, bundle=bundle)
            if correction is None:
                return await correct(rid, {'stage': 'review', **review})
            return 'rejected_review'
        try:
            return await self._evaluate(active, candidate, habits, report, cases, bundle)
        finally:
            (workspace / 'evaluation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    async def _replay_request(self, bundle, case):
        request = dict(case.get('request') or {'id': 'current', 'sender': 'human', 'created': 100, 'now': 100, 'mentioned': True})
        messages = case.get('messages', [])
        request.update(phase='before_wait', now=request['created'])
        before = [m for m in messages if m['created'] <= request['now']]
        initial = await self.runtime.request(bundle, before, request)
        request.update(phase='after_wait', now=request['created'] + initial['wait_ms'] / 1000)
        available = [m for m in messages if m['created'] <= request['now']]
        plan = await self.runtime.request(bundle, available, request)
        plan['wait_ms'] = initial['wait_ms']
        return append_context(case['input'], plan), plan

    async def _evaluate(self, active, candidate, habits, report, cases, bundle):
        replay, mapping = [], {}
        for case in cases:
            before_input, before_plan = await self._replay_request(active['bundle'], case)
            after_input, after_plan = await self._replay_request(bundle, case)
            before, after = await asyncio.wait_for(asyncio.gather(
                self.generate(active['prompt'], before_input, False),
                self.generate(candidate, after_input, False)), timeout=45)
            if not before.strip() or not after.strip():
                raise ValueError('Empty replay output')
            before_plan.update(await self.runtime.response(active['bundle'], before))
            after_plan.update(await self.runtime.response(bundle, after))
            candidate_is_x = bool(random.getrandbits(1))
            mapping[case['id']] = candidate_is_x
            replay.append({'case_id': case['id'], 'input': case['input'],
                           'x': after if candidate_is_x else before, 'y': before if candidate_is_x else after,
                           'x_behavior': after_plan if candidate_is_x else before_plan,
                           'y_behavior': before_plan if candidate_is_x else after_plan})
            if 'human_reference' in case:
                replay[-1]['human_reference'] = case['human_reference']
            if 'evaluation_focus' in case:
                replay[-1]['evaluation_focus'] = case['evaluation_focus']
        judgement = await self._json(JUDGE, {'core': CORE, 'cases': replay}, judge=True)
        report.update(replay=replay, candidate_is_x=mapping, judgement=judgement)
        if not accept_scores(judgement, mapping):
            self.store.add_revision(active['id'], 'rejected_replay', candidate, habits, report, bundle=bundle)
            return 'rejected_replay'
        if self.store.get('paused', False):
            self.store.add_revision(active['id'], 'paused_before_apply', candidate, habits, report, bundle=bundle)
            return 'paused_before_apply'
        return await self._activate(candidate, habits, report, active, bundle=bundle)

    async def _activate(self, prompt, habits, report, expected, base=None, bundle=None):
        bundle = expected['bundle'] if bundle is None else bundle
        if bundle_hash(bundle) not in self.runtime.validated:
            await self.runtime.verify(bundle)
        if await self.read_persona() != expected['prompt']:
            self.store.add_revision(expected['id'], 'external_edit', prompt, habits, report, bundle=bundle)
            return 'external_edit'
        rid = self.store.add_revision(expected['id'], 'applying', prompt, habits, report, base=base, bundle=bundle)
        self.store.put(applying=rid)
        # Keep applying on cancellation/failure: startup reconciles a possible successful write.
        await self.write_persona(prompt)
        if await self.read_persona() != prompt:
            raise RuntimeError('Persona write was not confirmed')
        self.store.activate(rid)
        return f'activated:{rid}'

    async def rollback(self):
        async with self.lock:
            active = self.store.revision()
            if active['parent'] is None:
                return 'already_baseline'
            previous = self.store.revision(active['parent'])
            result = await self._activate(previous['prompt'], previous['habits'], {'kind': 'rollback', 'target': previous['id']}, active, base=previous['base'], bundle=previous['bundle'])
            if result.startswith('activated:'):
                self.store.put(paused=True)
            return result

    async def runtime_failure(self, revision_id, detail):
        async with self.lock:
            active = self.store.revision()
            self.store.put(last_runtime_error={'revision': revision_id, 'detail': detail, 'at': time.time()})
            if active['id'] != revision_id or active['parent'] is None:
                return 'recorded'
            previous = self.store.revision(active['parent'])
            result = await self._activate(previous['prompt'], previous['habits'],
                                          {'kind': 'runtime_failure_rollback', 'failed_revision': revision_id, 'error': detail},
                                          active, base=previous['base'], bundle=previous['bundle'])
            if result.startswith('activated:'):
                self.store.put(paused=True, last_result='runtime_failure_rollback:' + str(revision_id))
            return result

    async def adopt(self):
        """Explicit admin adoption of external edits; old revisions remain available."""
        async with self.lock:
            current = await self.read_persona()
            if BEGIN in current or END in current:
                raise ValueError('Remove the generated RSI block before adopting a new base')
            active = self.store.revision()
            rid = self.store.add_revision(active['id'], 'candidate', current, [], {'kind': 'adopt'}, base=current)
            self.store.activate(rid)
            return f'adopted:{rid}'
