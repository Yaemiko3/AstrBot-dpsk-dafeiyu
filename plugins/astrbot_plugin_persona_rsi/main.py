"""AstrBot adapter. Background evaluation never sends chat messages."""
import asyncio
import hashlib
import json
import time
import uuid
from collections import deque

from astrbot.api import logger, sp
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.event.filter import PermissionType, permission_type
from astrbot.api.provider import ProviderRequest, LLMResponse
from astrbot.api.star import Context, Star, StarTools
from astrbot.core.message.message_event_result import ResultContentType

from .engine import EvolutionEngine
from .storage import Store
from .policy import PLANNER, REVIEWER, JUDGE
from .group_learning import GroupFeed, GROUP_PLANNER, GROUP_REVIEWER, group_batch
from .unified import UNIFIED_PLANNER, UNIFIED_REVIEWER
from .runtime import append_context, bundle_hash, default_bundle
from .passive import ObserveFilter, bind


class PersonaRSI(Star):
    def __init__(self, context: Context, config):
        super().__init__(context)
        self.config = config
        self.engine = None
        self.store = None
        self.worker = None
        self.group_feed = None
        self.feed_lock = asyncio.Lock()
        self.buffers = {}
        self.behavior_sessions = set()
        self.covered = {}
        self.fault_tasks = set()
        self.terminating = False

    async def initialize(self):
        if not self.config.get('enabled', False):
            logger.info('[PersonaRSI] disabled')
            return
        self.persona_id = self.config.get('persona_id', 'dafeiyu_custom')
        if not self.config.get('provider_id'):
            raise ValueError('PersonaRSI requires provider_id')
        # Separate state when the configured target persona changes.
        root = StarTools.get_data_dir('astrbot_plugin_persona_rsi')
        suffix = hashlib.sha256(self.persona_id.encode()).hexdigest()[:16]
        self.store = Store(root / suffix)
        self.engine = EvolutionEngine(self.store, self._generate, self._read, self._write,
                                      self.config.get('minimum_turns', 40), self.config.get('cooldown_hours', 6))
        await self.engine.initialize()
        if self.store.get('evolution_schema') != 'unified-v1':
            self.store.put(evolution_schema='unified-v1', watermark=0, group_watermarks={}, last_attempt=0)
        bind(self)
        if self.config.get('group_learning_enabled', True):
            if self.store.get('group_learning_policy') != 'shared-human-v1':
                # Reassess historical windows once under the new shared objective;
                # preserve the old reports and the active persona version.
                checkpoint = {'group_learning_policy': 'shared-human-v1', 'group_watermarks': {}}
                if self.store.get('last_source') == 'group':
                    checkpoint['last_attempt'] = 0
                self.store.put(**checkpoint)
            self.group_feed = GroupFeed(self.store, root.parent / 'astrbot_plugin_livingmemory/conversations.db',
                                        self._allow_group, self.config.get('group_history_days', 7),
                                        self.config.get('excluded_bot_ids', []))
            await self._sync_groups()
        else:
            self.store.put(group_source_status='disabled', group_allowed_sessions=[])
        self.worker = asyncio.create_task(self._loop())
        logger.info('[PersonaRSI] ready: %s, revision=%s, minimum_turns=%s', self.persona_id, self.store.get('active'), self.engine.minimum_turns)

    async def _read(self):
        persona = await self.context.persona_manager.get_persona(self.persona_id)
        return persona.system_prompt

    async def _write(self, prompt):
        await self.context.persona_manager.update_persona(self.persona_id, system_prompt=prompt)

    async def _generate(self, system, user, judge=False):
        provider = (self.config.get('review_provider_id') if judge else '') or self.config['provider_id']
        model = await self.context.provider_manager.get_provider_by_id(provider)
        client = getattr(model, 'client', None)
        # AstrBot 4.29.3's OpenAI adapter currently ignores per-call kwargs in
        # _prepare_chat_payload. Use its configured client for official DeepSeek
        # so bounded background requests really receive JSON/output controls.
        if client is not None and getattr(client.base_url, 'host', '') == 'api.deepseek.com':
            options = {'model': model.get_model(), 'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
                       'max_tokens': 6500 if system == UNIFIED_PLANNER else 2600, 'extra_body': {'thinking': {'type': 'disabled'}}, 'timeout': 50}
            if system in (PLANNER, REVIEWER, JUDGE, GROUP_PLANNER, GROUP_REVIEWER, UNIFIED_PLANNER, UNIFIED_REVIEWER):
                options['response_format'] = {'type': 'json_object'}
            completion = await client.chat.completions.create(**options)
            choice = completion.choices[0]
            if choice.finish_reason != 'stop' or not choice.message.content:
                raise ValueError('Incomplete background model output')
            return choice.message.content
        response = await self.context.llm_generate(chat_provider_id=provider, system_prompt=system, prompt=user,
                                                   max_tokens=6500 if system == UNIFIED_PLANNER else 2600)
        if response.role != 'assistant' or response.tools_call_name:
            raise ValueError('Unexpected evaluator response')
        return response.completion_text

    async def _allow_group(self, session, platform):
        sessions = self.config.get('allowed_sessions', [])
        if sessions and session not in sessions:
            return False
        cfg = self.context.get_config(session)
        settings = cfg.get('platform_settings', {})
        whitelist = [str(x).strip() for x in settings.get('id_whitelist', []) if str(x).strip()]
        if settings.get('enable_id_white_list') and whitelist and session not in whitelist and session.rsplit(':', 1)[-1] not in whitelist:
            return False
        service = await sp.get_async(scope='umo', scope_id=session, key='session_service_config', default={}) or {}
        if service.get('session_enabled') is False:
            return False
        plugin_config = await sp.get_async(scope='umo', scope_id=session, key='session_plugin_config', default={}) or {}
        disabled = plugin_config.get(session, {}).get('disabled_plugins', [])
        if any(name in disabled for name in ('Persona RSI', 'astrbot_plugin_persona_rsi', 'LivingMemory', 'astrbot_plugin_livingmemory')):
            return False
        cid = await self.context.conversation_manager.get_curr_conversation_id(session)
        conversation = await self.context.conversation_manager.get_conversation(session, cid) if cid else None
        selected, *_ = await self.context.persona_manager.resolve_selected_persona(
            umo=session, conversation_persona_id=getattr(conversation, 'persona_id', None), platform_name=platform)
        return selected == self.persona_id

    async def _sync_groups(self):
        if self.group_feed:
            async with self.feed_lock:
                try:
                    await self.group_feed.sync()
                except Exception as exc:
                    self.store.put(group_source_status='failed:' + type(exc).__name__, group_allowed_sessions=[])
                    logger.warning('[PersonaRSI] group source unavailable: %s', type(exc).__name__)

    async def _loop(self):
        try:
            while True:
                await asyncio.sleep(60)
                try:
                    await self._sync_groups()
                    result = await self.engine.evolve()
                    if not result.startswith(('collecting:', 'cooldown', 'paused')):
                        logger.info('[PersonaRSI] iteration: %s', result)
                except Exception as exc:
                    logger.warning('[PersonaRSI] iteration failed: %s', type(exc).__name__)
        except asyncio.CancelledError:
            raise

    def observe_event(self, event):
        text = event.get_message_str().strip()
        sender = str(event.get_sender_id())
        if not text or text.startswith('/') or sender in {str(x) for x in self.config.get('excluded_bot_ids', [])}:
            return None
        if sender == str(getattr(event.message_obj, 'self_id', '')):
            return None
        mid = event.get_extra('_rsi_message_id') or str(getattr(event.message_obj, 'message_id', '') or uuid.uuid4().hex)
        event.set_extra('_rsi_message_id', mid)
        session = event.unified_msg_origin
        if session not in self.buffers and len(self.buffers) >= 200:
            oldest = next(iter(self.buffers))
            self.buffers.pop(oldest)
            self.behavior_sessions.discard(oldest)
        rows = self.buffers.setdefault(session, deque(maxlen=64))
        existing = next((m for m in rows if m['id'] == mid), None)
        if existing:
            return existing
        message = {'id': mid, 'sender': sender, 'created': time.time(), 'text': text[:1500]}
        rows.append(message)
        return message

    @filter.custom_filter(ObserveFilter, False)
    async def observe_messages(self, event: AstrMessageEvent):
        pass

    def behavior_fault(self, revision_id, exc):
        logger.warning('[PersonaRSI] behavior fallback: %s', type(exc).__name__)
        if self.terminating:
            return
        task = asyncio.create_task(self.engine.runtime_failure(revision_id, type(exc).__name__ + ': ' + str(exc)[:200]))
        self.fault_tasks.add(task)
        task.add_done_callback(self.fault_tasks.discard)

    @filter.on_llm_request()
    async def capture_request(self, event: AstrMessageEvent, req: ProviderRequest):
        if not self.engine:
            return
        selected, *_ = await self.context.persona_manager.resolve_selected_persona(
            umo=event.unified_msg_origin,
            conversation_persona_id=getattr(req.conversation, 'persona_id', None),
            platform_name=event.get_platform_name(),
        )
        if selected != self.persona_id:
            return
        sessions = self.config.get('allowed_sessions', [])
        if sessions and event.unified_msg_origin not in sessions:
            return
        # Raw incoming text only: do not feed LivingMemory's summaries back as fresh evidence.
        user = event.get_message_str().strip()
        if not user or user.startswith('/'):
            return
        if event.get_extra('_persona_rsi_capture'):
            return
        self.behavior_sessions.add(event.unified_msg_origin)
        message = self.observe_event(event)
        if message is None:
            return
        message_id = message['id']
        self.covered = {key: expiry for key, expiry in self.covered.items() if expiry > time.time()}
        key = event.unified_msg_origin + ':' + message_id
        if key in self.covered:
            event.stop_event()
            return
        active = self.store.revision()
        # AstrBot builds the persona before this hook. If an activation happened
        # between those steps, align it with this request's behavior snapshot.
        system_prompt = req.system_prompt or ''
        if active['prompt'] not in system_prompt and active['parent'] is not None:
            previous_prompt = self.store.revision(active['parent'])['prompt']
            if previous_prompt and previous_prompt in system_prompt:
                req.system_prompt = system_prompt.replace(previous_prompt, active['prompt'], 1)
        request = {'id': message_id, 'sender': message['sender'], 'created': message['created'],
                   'now': time.time(), 'phase': 'before_wait', 'mentioned': bool(getattr(event, 'is_at_or_wake_command', False))}
        bundle = active['bundle']
        try:
            initial = await self.engine.runtime.request(bundle, list(self.buffers[event.unified_msg_origin]), request)
            if initial['wait_ms']:
                await asyncio.sleep(initial['wait_ms'] / 1000)
            request.update(now=time.time(), phase='after_wait')
            messages = list(self.buffers[event.unified_msg_origin])
            plan = await self.engine.runtime.request(bundle, messages, request)
            plan['wait_ms'] = initial['wait_ms']
        except Exception as exc:
            self.behavior_fault(active['id'], exc)
            bundle = default_bundle()
            messages = list(self.buffers[event.unified_msg_origin])
            plan = {'wait_ms': 0, 'context_ids': [], 'merge_ids': [], 'messages': []}
        req.prompt = append_context(req.prompt or '', plan)
        captured = {
            'key': event.unified_msg_origin + ':' + str(message_id),
            'session': event.unified_msg_origin, 'sender': event.get_sender_id(), 'user': user,
            'bundle': bundle, 'revision': active['id'], 'merge_ids': plan['merge_ids'],
            'trace': {'revision': active['id'], 'request': request, 'messages': messages, 'plan': plan},
        }
        event.set_extra('_persona_rsi_capture', captured)

    @filter.on_llm_response()
    async def capture_response(self, event: AstrMessageEvent, response: LLMResponse):
        captured = event.get_extra('_persona_rsi_capture')
        if captured and response and response.role == 'assistant' and not response.tools_call_name and not response.is_chunk:
            captured['assistant'] = response.completion_text

    @filter.on_decorating_result(priority=-100)
    async def apply_send_timing(self, event: AstrMessageEvent):
        captured = event.get_extra('_persona_rsi_capture')
        if not captured or captured.get('timed') or not self.engine:
            return
        result = event.get_result()
        if not result or not (result.is_llm_result() or result.result_content_type == ResultContentType.STREAMING_RESULT):
            return
        captured['timed'] = True
        try:
            plan = await self.engine.runtime.response(captured['bundle'], result.get_plain_text() or captured.get('assistant', ''))
            captured['trace']['response_plan'] = plan
            if plan['delay_ms']:
                await asyncio.sleep(plan['delay_ms'] / 1000)
        except Exception as exc:
            self.behavior_fault(captured['revision'], exc)

    @filter.after_message_sent()
    async def record_turn(self, event: AstrMessageEvent, *_args):
        captured = event.get_extra('_persona_rsi_capture')
        if not self.engine or not captured or captured.get('recorded'):
            return
        result = event.get_result()
        # This hook confirms the reply reached AstrBot's send stage, not remote delivery.
        if not result or not (result.is_llm_result() or result.result_content_type == ResultContentType.STREAMING_RESULT):
            return
        assistant = result.get_plain_text() or captured.get('assistant', '')
        request_time = captured['trace']['request']['created']
        captured['trace']['messages'] = [m for m in self.buffers.get(captured['session'], [])
                                        if request_time - 180 <= m['created'] <= request_time + 3][-12:]
        if self.store.record(captured['key'], captured['session'], captured['sender'], captured['user'], assistant, captured.get('trace')):
            captured['recorded'] = True
            for mid in captured.get('merge_ids', []):
                self.covered[captured['session'] + ':' + mid] = time.time() + 120

    @filter.command_group('prsi')
    def prsi(self):
        """人格 RSI 管理。"""
        pass

    def _status(self):
        if not self.engine:
            return '人格 RSI 未启用，请先配置模型并启用插件。'
        active = self.store.revision()
        return (f'人格：{self.persona_id}\n版本：{active["id"]}，习惯 {len(active["habits"])} 条\n'
                f'状态：{"暂停" if self.store.get("paused") else "运行"}\n'
                f'统一改进：人格 / 参数 / 行为代码，代码版本 {bundle_hash(active["bundle"])}\n'
                f'新对话：{len(self.store.pending_turns())}/{self.engine.minimum_turns}\n'
                f'群聊学习：{self.store.get("group_source_status", "disabled")}，已导入 {self.store.group_count()} 条真人候选消息，待评估 {len(group_batch(self.store))} 个片段\n'
                f'最近结果：{self.store.get("last_result", "尚未迭代")}')

    @permission_type(PermissionType.ADMIN)
    @prsi.command('status')
    async def status(self, event: AstrMessageEvent):
        yield event.plain_result(self._status())

    @permission_type(PermissionType.ADMIN)
    @prsi.command('habits')
    async def habits(self, event: AstrMessageEvent):
        if not self.engine:
            yield event.plain_result(self._status())
            return
        rows = self.store.revision()['habits']
        yield event.plain_result('\n'.join(h['dimension'] + '：' + h['text'] for h in rows) or '尚未采用新的习惯。')

    @permission_type(PermissionType.ADMIN)
    @prsi.command('runtime')
    async def runtime_settings(self, event: AstrMessageEvent):
        if not self.engine:
            yield event.plain_result(self._status())
            return
        active = self.store.revision()
        yield event.plain_result(f'统一版本 v{active["id"]}\n行为代码 {bundle_hash(active["bundle"])}\n' + json.dumps(active['bundle']['parameters'], ensure_ascii=False, indent=2))

    @permission_type(PermissionType.ADMIN)
    @prsi.command('history')
    async def history(self, event: AstrMessageEvent):
        if not self.engine:
            yield event.plain_result(self._status())
            return
        lines = []
        for row in self.store.history():
            report = json.loads(row['report'])
            reason = report.get('proposal', {}).get('reason', report.get('kind', ''))
            targets = ','.join(report.get('targets', []))
            lines.append(f'v{row["id"]} {row["status"]} [{targets}]：{reason[:180]}')
        yield event.plain_result('\n'.join(lines))

    @permission_type(PermissionType.ADMIN)
    @prsi.command('pause')
    async def pause(self, event: AstrMessageEvent):
        if self.engine:
            self.store.put(paused=True)
        yield event.plain_result(self._status())

    @permission_type(PermissionType.ADMIN)
    @prsi.command('resume')
    async def resume(self, event: AstrMessageEvent):
        if self.engine:
            self.store.put(paused=False)
        yield event.plain_result(self._status())

    @permission_type(PermissionType.ADMIN)
    @prsi.command('run')
    async def run(self, event: AstrMessageEvent):
        if not self.engine:
            yield event.plain_result(self._status())
            return
        try:
            await self._sync_groups()
            result = await self.engine.evolve(force=True)
            yield event.plain_result('本次结果：' + result)
        except Exception as exc:
            yield event.plain_result('本次迭代失败，未确认的版本不会被认作已生效：' + type(exc).__name__)

    @permission_type(PermissionType.ADMIN)
    @prsi.command('rollback')
    async def rollback(self, event: AstrMessageEvent):
        if not self.engine:
            yield event.plain_result(self._status())
            return
        yield event.plain_result(await self.engine.rollback())

    @permission_type(PermissionType.ADMIN)
    @prsi.command('adopt')
    async def adopt(self, event: AstrMessageEvent):
        if not self.engine:
            yield event.plain_result(self._status())
            return
        try:
            yield event.plain_result(await self.engine.adopt())
        except ValueError as exc:
            yield event.plain_result(str(exc))

    async def terminate(self):
        self.terminating = True
        bind(None)
        if self.worker:
            self.worker.cancel()
            try:
                await self.worker
            except asyncio.CancelledError:
                pass
        if self.fault_tasks:
            await asyncio.gather(*self.fault_tasks, return_exceptions=True)
        if self.engine:
            async with self.engine.lock:
                self.store.close()
