"""Initial editable behavior module. RSI versions this source with its persona."""


def plan_request(messages, request, settings):
    wait = settings['coalesce_window_ms'] if request.get('phase') == 'before_wait' else 0
    eligible = [m for m in messages if m['id'] != request['id']
                and 0 <= request['now'] - m['created'] <= settings['context_max_age_seconds']]
    limit = settings['context_message_limit']
    selected = eligible[-limit:] if limit else []
    merged = [m['id'] for m in selected if m['sender'] == request['sender']
              and 0 < m['created'] - request['created'] <= settings['coalesce_window_ms'] / 1000]
    return {'wait_ms': wait, 'context_ids': [m['id'] for m in selected], 'merge_ids': merged}


def plan_response(text, settings):
    return {'delay_ms': settings['send_delay_ms']}
