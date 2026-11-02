"""Adapter integration tests; run with AstrBot's environment and core on PYTHONPATH."""
import importlib.util
import asyncio
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch, AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugins'))
HAS_ASTRBOT = importlib.util.find_spec('astrbot') is not None
if HAS_ASTRBOT:
    from astrbot_plugin_persona_rsi.main import PersonaRSI
    from astrbot.api.provider import ProviderRequest, LLMResponse
    from astrbot.core.message.message_event_result import MessageEventResult, ResultContentType


@unittest.skipUnless(HAS_ASTRBOT, 'AstrBot SDK not installed in this interpreter')
class AdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.live = SimpleNamespace(system_prompt='妹妹人格')
        self.selected = 'dafeiyu_custom'
        async def get(_): return self.live
        async def update(_, system_prompt): self.live.system_prompt = system_prompt
        async def resolve(**kwargs): return self.selected, None, None, False
        context = SimpleNamespace(persona_manager=SimpleNamespace(get_persona=get, update_persona=update, resolve_selected_persona=resolve))
        self.plugin = PersonaRSI(context, {'enabled': True, 'provider_id': 'test', 'minimum_turns': 10})
        with patch('astrbot_plugin_persona_rsi.main.StarTools.get_data_dir', return_value=Path(self.temp.name)):
            await self.plugin.initialize()

    async def asyncTearDown(self):
        await self.plugin.terminate()
        self.temp.cleanup()

    def event(self, stream=False):
        extras = {}
        result = MessageEventResult().message('已经处理过表情标记的最终文本')
        result.set_result_content_type(ResultContentType.STREAMING_RESULT if stream else ResultContentType.LLM_RESULT)
        return SimpleNamespace(unified_msg_origin='test:group:one', message_obj=SimpleNamespace(message_id='42'),
                               get_message_str=lambda: '你这句太啰嗦了', get_sender_id=lambda: 'test-user',
                               get_platform_name=lambda: 'aiocqhttp', get_extra=lambda key: extras.get(key),
                               set_extra=lambda key, value: extras.update({key: value}), get_result=lambda: result)

    async def test_nonstream_capture_uses_raw_input_and_final_reply_once(self):
        event = self.event()
        await self.plugin.capture_request(event, ProviderRequest(prompt='Injected memory must not become evidence'))
        await self.plugin.capture_response(event, LLMResponse(role='assistant', completion_text='draft'))
        await self.plugin.record_turn(event)
        await self.plugin.record_turn(event)
        rows = self.plugin.store.pending_turns()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['user'], '你这句太啰嗦了')
        self.assertEqual(rows[0]['assistant'], '已经处理过表情标记的最终文本')

    async def test_streaming_capture(self):
        event = self.event(stream=True)
        await self.plugin.capture_request(event, ProviderRequest())
        await self.plugin.capture_response(event, LLMResponse(role='assistant', completion_text='完整流式结果'))
        event.get_result().chain.clear()
        await self.plugin.record_turn(event)
        self.assertEqual(self.plugin.store.pending_turns()[0]['assistant'], '完整流式结果')

    async def test_other_persona_not_captured(self):
        self.selected = 'other'
        event = self.event()
        await self.plugin.capture_request(event, ProviderRequest())
        await self.plugin.record_turn(event)
        self.assertEqual(self.plugin.store.pending_turns(), [])

    async def test_failed_runner_not_captured(self):
        event = self.event()
        await self.plugin.capture_request(event, ProviderRequest())
        event.get_result().set_result_content_type(ResultContentType.AGENT_RUNNER_ERROR)
        await self.plugin.record_turn(event)
        self.assertEqual(self.plugin.store.pending_turns(), [])

    async def test_deepseek_background_options_do_not_mutate_chat_provider(self):
        from astrbot_plugin_persona_rsi.policy import PLANNER
        calls = []
        async def create(**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop', message=SimpleNamespace(content='{"decision":"no_change"}'))])
        model = SimpleNamespace(client=SimpleNamespace(base_url=SimpleNamespace(host='api.deepseek.com'),
                                                        chat=SimpleNamespace(completions=SimpleNamespace(create=create))),
                                get_model=lambda: 'deepseek-flash', provider_config={'keep': 'unchanged'})
        async def get(_): return model
        self.plugin.context.provider_manager = SimpleNamespace(get_provider_by_id=get)
        text = await self.plugin._generate(PLANNER, '{}')
        self.assertIn('no_change', text)
        self.assertEqual(calls[0]['response_format'], {'type': 'json_object'})
        self.assertEqual(calls[0]['extra_body'], {'thinking': {'type': 'disabled'}})
        self.assertEqual(model.provider_config, {'keep': 'unchanged'})

    async def test_live_request_uses_same_human_habits_in_other_groups(self):
        from astrbot_plugin_persona_rsi.policy import compose
        habits = [{'dimension': 'banter', 'text': '根据语境自然接梗。', 'evidence_ids': [1, 2, 3], 'scope': '*'}]
        rid = self.plugin.store.add_revision(1, 'active', compose('妹妹人格', habits), habits, {})
        self.plugin.store.activate(rid)
        event = self.event()
        req = ProviderRequest(system_prompt=compose('妹妹人格', habits) + '\n工具说明')
        await self.plugin.capture_request(event, req)
        self.assertIn('根据语境自然接梗', req.system_prompt)
        event.unified_msg_origin = 'test:group:two'
        req = ProviderRequest(system_prompt=compose('妹妹人格', habits) + '\n工具说明')
        await self.plugin.capture_request(event, req)
        self.assertIn('根据语境自然接梗', req.system_prompt)
        self.assertIn('工具说明', req.system_prompt)

    async def test_group_feed_respects_whitelist_persona_and_session_shutdown(self):
        self.plugin.context.get_config = lambda _: {'platform_settings': {'enable_id_white_list': True, 'id_whitelist': ['one']}}
        self.plugin.context.conversation_manager = SimpleNamespace(get_curr_conversation_id=AsyncMock(return_value=None))
        with patch('astrbot_plugin_persona_rsi.main.sp.get_async', new=AsyncMock(return_value={})):
            self.assertTrue(await self.plugin._allow_group('qq:GroupMessage:one', 'aiocqhttp'))
            self.assertFalse(await self.plugin._allow_group('qq:GroupMessage:two', 'aiocqhttp'))
            self.selected = 'other'
            self.assertFalse(await self.plugin._allow_group('qq:GroupMessage:one', 'aiocqhttp'))
        self.selected = 'dafeiyu_custom'
        with patch('astrbot_plugin_persona_rsi.main.sp.get_async', new=AsyncMock(return_value={'session_enabled': False})):
            self.assertFalse(await self.plugin._allow_group('qq:GroupMessage:one', 'aiocqhttp'))

    async def test_shared_learning_migration_revisits_history_only_once(self):
        self.plugin.store.put(group_learning_policy='old', group_watermarks={'one': 123}, last_source='group', last_attempt=123)
        active = self.plugin.store.get('active')
        async def restart():
            context = self.plugin.context
            await self.plugin.terminate()
            self.plugin = PersonaRSI(context, {'enabled': True, 'provider_id': 'test'})
            with patch('astrbot_plugin_persona_rsi.main.StarTools.get_data_dir', return_value=Path(self.temp.name)):
                await self.plugin.initialize()
        await restart()
        self.assertEqual(self.plugin.store.get('active'), active)
        self.assertEqual(self.plugin.store.get('group_watermarks'), {})
        self.assertEqual(self.plugin.store.get('last_attempt'), 0)
        self.plugin.store.put(group_watermarks={'one': 456}, last_attempt=456)
        await restart()
        self.assertEqual(self.plugin.store.get('group_watermarks'), {'one': 456})
        self.assertEqual(self.plugin.store.get('last_attempt'), 456)

    async def test_same_sender_followup_is_merged_only_after_reply_send(self):
        active = self.plugin.store.revision()
        bundle = active['bundle']
        bundle['parameters'].update(coalesce_window_ms=120, context_message_limit=4)
        rid = self.plugin.store.add_revision(active['id'], 'active', active['prompt'], [], {}, bundle=bundle)
        self.plugin.store.activate(rid)
        first = self.event()
        req = ProviderRequest(prompt='原始问题')
        task = asyncio.create_task(self.plugin.capture_request(first, req))
        await asyncio.sleep(0.03)
        follow = self.event()
        follow.message_obj.message_id = '43'
        follow.get_message_str = lambda: '补充一下，我说的是那个角色'
        stopped = []
        follow.stop_event = lambda: stopped.append(True)
        self.plugin.observe_event(follow)
        await task
        self.assertIn('我说的是那个角色', req.prompt)
        self.assertNotIn('test:group:one:43', self.plugin.covered)
        await self.plugin.record_turn(first)
        self.assertIn('test:group:one:43', self.plugin.covered)
        await self.plugin.capture_request(follow, ProviderRequest(prompt='补充'))
        self.assertEqual(stopped, [True])

    async def test_failed_reply_does_not_consume_followup(self):
        first = self.event()
        await self.plugin.capture_request(first, ProviderRequest(prompt='问题'))
        first.get_extra('_persona_rsi_capture')['merge_ids'] = ['43']
        first.get_result().set_result_content_type(ResultContentType.AGENT_RUNNER_ERROR)
        await self.plugin.record_turn(first)
        self.assertEqual(self.plugin.covered, {})

    async def test_send_delay_is_actual_runtime_behavior(self):
        active = self.plugin.store.revision()
        bundle = active['bundle']
        bundle['parameters']['send_delay_ms'] = 100
        rid = self.plugin.store.add_revision(active['id'], 'active', active['prompt'], [], {}, bundle=bundle)
        self.plugin.store.activate(rid)
        event = self.event()
        await self.plugin.capture_request(event, ProviderRequest(prompt='问题'))
        with patch('astrbot_plugin_persona_rsi.main.asyncio.sleep', new=AsyncMock()) as sleep:
            await self.plugin.apply_send_timing(event)
            await self.plugin.apply_send_timing(event)
            sleep.assert_awaited_once_with(0.1)

    async def test_passive_observer_does_not_wake_message_pipeline(self):
        from astrbot_plugin_persona_rsi.passive import ObserveFilter
        event = self.event()
        self.plugin.behavior_sessions.add(event.unified_msg_origin)
        self.assertFalse(ObserveFilter(False).filter(event, {}))
        self.assertEqual(len(self.plugin.buffers[event.unified_msg_origin]), 1)
