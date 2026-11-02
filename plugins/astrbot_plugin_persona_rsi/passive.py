"""Observe already-authorized conversations without waking the bot."""
import weakref
from astrbot.api.event.filter import CustomFilter

ACTIVE = None


def bind(plugin):
    global ACTIVE
    ACTIVE = weakref.ref(plugin) if plugin else None


class ObserveFilter(CustomFilter):
    def filter(self, event, cfg):
        plugin = ACTIVE() if ACTIVE else None
        if not plugin or not plugin.engine or plugin.terminating:
            return False
        session = event.unified_msg_origin
        if session not in plugin.behavior_sessions and session not in plugin.store.get('group_allowed_sessions', []):
            return False
        settings = cfg.get('platform_settings', {})
        whitelist = [str(x).strip() for x in settings.get('id_whitelist', []) if str(x).strip()]
        if settings.get('enable_id_white_list') and whitelist and session not in whitelist and session.rsplit(':', 1)[-1] not in whitelist:
            return False
        plugin.observe_event(event)
        return False
