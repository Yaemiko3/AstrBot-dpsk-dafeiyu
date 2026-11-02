"""Run one isolated RSI iteration on recent LivingMemory turns; never updates AstrBot."""
import argparse
import asyncio
import json
import hashlib
from pathlib import Path
import sqlite3
import sys
import tempfile
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugins'))
from astrbot_plugin_persona_rsi.engine import EvolutionEngine
from astrbot_plugin_persona_rsi.storage import Store
from astrbot_plugin_persona_rsi.policy import BEGIN, PLANNER, REVIEWER, JUDGE
from astrbot_plugin_persona_rsi.group_learning import GroupFeed, GROUP_PLANNER, GROUP_REVIEWER, group_batch
from astrbot_plugin_persona_rsi.unified import UNIFIED_PLANNER, UNIFIED_REVIEWER


async def preview(args):
    core = args.core.resolve()
    config = json.loads((core / 'data/cmd_config.json').read_text(encoding='utf-8-sig'))
    provider = next(p for p in config['provider'] if p['id'] == args.provider)
    source = next(p for p in config['provider_sources'] if p['id'] == provider['provider_source_id'])
    key = source['key']
    if isinstance(key, list):
        key = next(k for k in key if k)
    db_path = core / 'data/data_v4.db'
    with sqlite3.connect(db_path.as_uri() + '?mode=ro', uri=True) as db:
        row = db.execute('SELECT system_prompt FROM personas WHERE persona_id=?', (args.persona,)).fetchone()
        if not row:
            raise ValueError('Target persona not found')
        live = row[0]
    db.close()
    db_path = core / 'data/plugin_data/astrbot_plugin_livingmemory/conversations.db'
    with sqlite3.connect(db_path.as_uri() + '?mode=ro', uri=True) as db:
        rows = db.execute("SELECT id,session_id,content FROM messages WHERE role='assistant' ORDER BY id DESC LIMIT ?", (args.turns,)).fetchall()
        pairs = []
        for mid, session, reply in reversed(rows):
            prior = db.execute("SELECT sender_id,content FROM messages WHERE session_id=? AND id<? AND role='user' ORDER BY id DESC LIMIT 1", (session, mid)).fetchone()
            if prior and prior[1].strip():
                pairs.append((str(mid), session, prior[0], prior[1], reply))
    db.close()
    if not args.group_session and len(pairs) < 10:
        raise ValueError('Need at least ten nonempty message pairs')

    def complete(system, user):
        payload = {'model': provider['model'], 'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}], 'max_tokens': 6500 if system == UNIFIED_PLANNER else 2600}
        if 'api.deepseek.com' in source['api_base']:
            payload['thinking'] = {'type': 'disabled'}
        if system in (PLANNER, REVIEWER, JUDGE, GROUP_PLANNER, GROUP_REVIEWER, UNIFIED_PLANNER, UNIFIED_REVIEWER):
            payload['response_format'] = {'type': 'json_object'}
        request = urllib.request.Request(source['api_base'].rstrip('/') + '/chat/completions',
                                         data=json.dumps(payload).encode(),
                                         headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, timeout=50) as response:
            choice = json.load(response)['choices'][0]
            if choice['finish_reason'] != 'stop' or not choice['message']['content']:
                raise ValueError('Incomplete preview output: ' + str(choice['finish_reason']))
            return choice['message']['content']

    calls = 0
    async def generate(system, user, judge=False):
        nonlocal calls
        calls += 1
        return await asyncio.to_thread(complete, system, user)

    simulated = live
    async def read(): return simulated
    async def write(prompt):
        nonlocal simulated
        simulated = prompt

    with tempfile.TemporaryDirectory(prefix='persona-rsi-preview-') as directory:
        store = Store(directory)
        try:
            suffix = hashlib.sha256(args.persona.encode()).hexdigest()[:16]
            state_path = core / 'data/plugin_data/astrbot_plugin_persona_rsi' / suffix / 'persona_rsi.db'
            if state_path.is_file():
                state = sqlite3.connect(state_path.as_uri() + '?mode=ro', uri=True)
                try:
                    active_id = json.loads(state.execute("SELECT value FROM meta WHERE key='active'").fetchone()[0])
                    prompt, base, habits = state.execute('SELECT prompt,base,habits FROM revisions WHERE id=?', (active_id,)).fetchone()
                    if prompt != live:
                        raise ValueError('Live persona differs from RSI state')
                    bundle = None
                    if 'bundle' in {r[1] for r in state.execute('PRAGMA table_info(revisions)')}:
                        bundle = json.loads(state.execute('SELECT bundle FROM revisions WHERE id=?', (active_id,)).fetchone()[0]) or None
                    rid = store.add_revision(None, 'active', live, json.loads(habits), {'kind': 'preview'}, base=base, bundle=bundle)
                    store.put(active=rid, base=base)
                finally:
                    state.close()
            engine = EvolutionEngine(store, generate, read, write, minimum_turns=len(pairs))
            await engine.initialize()
            if args.group_session:
                async def allow(session, platform): return session in args.group_session
                feed = GroupFeed(store, db_path, allow)
                await feed.sync()
                print(json.dumps({'group_messages': store.group_count(), 'eligible_windows': len(group_batch(store))}, ensure_ascii=False), flush=True)
            else:
                for pair in pairs:
                    store.record(*pair)
            result = await engine.evolve(force=True)
            latest = store.history()[0]
            report = json.loads(latest['report'])
            print(json.dumps({'result': result, 'model_calls': calls,
                              'reason': report.get('proposal', {}).get('reason'),
                              'review': report.get('review'),
                              'evidence_error': report.get('evidence_error'),
                              'targets': report.get('targets', []),
                              'functional_tests_passed': report.get('functional_tests', {}).get('passed'),
                              'judgement': report.get('judgement'),
                              'candidate_habits': report.get('proposal', {}).get('changes', {}).get('persona', {}).get('habits', store.revision(latest['id'])['habits']),
                              'candidate_parameters': report.get('proposal', {}).get('changes', {}).get('parameters'),
                              'live_persona_modified': False, 'live_runtime_modified': False}, ensure_ascii=False, indent=2))
        finally:
            store.close()


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--core', type=Path, required=True)
    parser.add_argument('--provider', default='deepseek/deepseek-flash')
    parser.add_argument('--persona', default='dafeiyu_custom')
    parser.add_argument('--turns', type=int, default=40, choices=range(10, 61))
    parser.add_argument('--group-session', action='append', help='Read-only group preview; repeat for multiple UMO sessions')
    asyncio.run(preview(parser.parse_args()))
