"""Private local history; every activation is a version, including rollback."""
import json
import sqlite3
import time
from pathlib import Path
from .runtime import default_bundle


class Store:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.directory / 'persona_rsi.db')
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS turns(
          id INTEGER PRIMARY KEY AUTOINCREMENT, event_key TEXT UNIQUE NOT NULL,
          session TEXT NOT NULL, sender TEXT NOT NULL, created REAL NOT NULL,
          user TEXT NOT NULL, assistant TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS revisions(
          id INTEGER PRIMARY KEY AUTOINCREMENT, parent INTEGER, created REAL NOT NULL,
          status TEXT NOT NULL, prompt TEXT NOT NULL, base TEXT NOT NULL, habits TEXT NOT NULL,
          report TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS group_messages(
          id INTEGER PRIMARY KEY AUTOINCREMENT, source_key TEXT UNIQUE NOT NULL,
          session TEXT NOT NULL, sender TEXT NOT NULL, created REAL NOT NULL,
          content TEXT NOT NULL, role TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS group_messages_session ON group_messages(session,id);
        ''')
        self.db.commit()
        # Additive migration preserves earlier persona-only versions and evidence.
        if 'bundle' not in {r[1] for r in self.db.execute('PRAGMA table_info(revisions)')}:
            self.db.execute("ALTER TABLE revisions ADD COLUMN bundle TEXT NOT NULL DEFAULT '{}'")
        if 'trace' not in {r[1] for r in self.db.execute('PRAGMA table_info(turns)')}:
            self.db.execute("ALTER TABLE turns ADD COLUMN trace TEXT NOT NULL DEFAULT '{}'")
        self.db.commit()

    def get(self, key, default=None):
        row = self.db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def put(self, **values):
        with self.db:
            for key, value in values.items():
                self.db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', (key, json.dumps(value, ensure_ascii=False)))

    def record(self, key, session, sender, user, assistant, trace=None):
        if not user.strip() or not assistant.strip():
            return False
        with self.db:
            result = self.db.execute('INSERT OR IGNORE INTO turns(event_key,session,sender,created,user,assistant,trace) VALUES(?,?,?,?,?,?,?)',
                                     (key, session, sender, time.time(), user[:2000], assistant[:2500], json.dumps(trace or {}, ensure_ascii=False)))
        return result.rowcount == 1

    def pending_turns(self, limit=60):
        rows = [dict(r) for r in self.db.execute('SELECT * FROM turns WHERE id>? ORDER BY id LIMIT ?', (self.get('watermark', 0), limit))]
        for row in rows:
            row['trace'] = json.loads(row['trace'])
        return rows

    def record_group(self, rows, **checkpoint):
        """Import and checkpoint together: a restart cannot skip an uncommitted page."""
        with self.db:
            self.db.executemany('INSERT OR IGNORE INTO group_messages(source_key,session,sender,created,content,role) VALUES(?,?,?,?,?,?)', rows)
            for key, value in checkpoint.items():
                self.db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', (key, json.dumps(value, ensure_ascii=False)))

    def group_count(self):
        return self.db.execute("SELECT count(*) FROM group_messages WHERE role='user'").fetchone()[0]

    def add_revision(self, parent, status, prompt, habits, report, base=None, bundle=None):
        if bundle is None:
            bundle = self.revision(parent)['bundle'] if parent is not None else default_bundle()
        with self.db:
            cur = self.db.execute('INSERT INTO revisions(parent,created,status,prompt,base,habits,report,bundle) VALUES(?,?,?,?,?,?,?,?)',
                                  (parent, time.time(), status, prompt, self.get('base', prompt) if base is None else base, json.dumps(habits, ensure_ascii=False), json.dumps(report, ensure_ascii=False), json.dumps(bundle, ensure_ascii=False)))
        return cur.lastrowid

    def revision(self, rid=None):
        rid = self.get('active') if rid is None else rid
        row = self.db.execute('SELECT * FROM revisions WHERE id=?', (rid,)).fetchone()
        if not row:
            raise ValueError('Revision not found')
        result = dict(row)
        result['habits'] = json.loads(result['habits'])
        result['report'] = json.loads(result['report'])
        result['bundle'] = json.loads(result['bundle']) or default_bundle()
        return result

    def mark(self, rid, status):
        with self.db:
            self.db.execute('UPDATE revisions SET status=? WHERE id=?', (status, rid))

    def activate(self, rid):
        # SQLite pointer and status change together; caller has already updated AstrBot.
        with self.db:
            self.db.execute("UPDATE revisions SET status='archived' WHERE status='active'")
            self.db.execute("UPDATE revisions SET status='active' WHERE id=?", (rid,))
            for key, value in {'active': rid, 'base': self.revision(rid)['base'], 'applying': None, 'last_result': 'activated:' + str(rid)}.items():
                self.db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', (key, json.dumps(value)))
        self.export()

    def export(self):
        active = self.revision()
        tmp = self.directory / 'active_persona.md.tmp'
        tmp.write_text(active['prompt'], encoding='utf-8')
        tmp.replace(self.directory / 'active_persona.md')
        for name, value in {'active_runtime.json': json.dumps(active['bundle']['parameters'], ensure_ascii=False, indent=2),
                            'active_behavior.py': active['bundle']['code']}.items():
            temp = self.directory / (name + '.tmp')
            temp.write_text(value, encoding='utf-8')
            temp.replace(self.directory / name)

    def history(self, limit=10):
        return [dict(r) for r in self.db.execute('SELECT id,parent,created,status,report FROM revisions ORDER BY id DESC LIMIT ?', (limit,))]

    def close(self):
        self.db.close()
