"""Install this repository's Persona RSI plugin into an explicit local AstrBot core."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import shutil
import sqlite3


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--core', required=True, type=Path)
    parser.add_argument('--enable', action='store_true')
    parser.add_argument('--provider', default='deepseek/deepseek-flash')
    args = parser.parse_args()
    core = args.core.resolve()
    if not (core / 'astrbot/core/persona_mgr.py').is_file():
        parser.error('Not an AstrBot core directory')
    source = Path(__file__).resolve().parents[1] / 'plugins/astrbot_plugin_persona_rsi'
    target = core / 'data/plugins/astrbot_plugin_persona_rsi'
    config_path = core / 'data/config/astrbot_plugin_persona_rsi_config.json'
    backup = core / 'data/backups' / ('persona-rsi-install-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
    backup.mkdir(parents=True, exist_ok=False)
    if target.exists():
        shutil.copytree(target, backup / 'plugin', ignore=shutil.ignore_patterns('__pycache__'))
    if config_path.exists():
        shutil.copy2(config_path, backup / config_path.name)
        config = json.loads(config_path.read_text(encoding='utf-8-sig'))
    else:
        config = {}
    schema = json.loads((source / '_conf_schema.json').read_text(encoding='utf-8'))
    for key, item in schema.items():
        config.setdefault(key, item['default'])
    state_root = core / 'data/plugin_data/astrbot_plugin_persona_rsi'
    for database in state_root.glob('*/persona_rsi.db'):
        destination = backup / 'state' / database.parent.name
        destination.mkdir(parents=True, exist_ok=True)
        original = sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)
        copied = sqlite3.connect(destination / database.name)
        try:
            original.backup(copied)
        finally:
            copied.close()
            original.close()
    if args.enable:
        config.update(enabled=True, provider_id=args.provider)
    shutil.copytree(source, target, dirs_exist_ok=True, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    config_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = config_path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(config_path)
    print('Installed:', target)
    print('Enabled:', config['enabled'])
    print('Restart/reload AstrBot to load the plugin.')


if __name__ == '__main__':
    main()
