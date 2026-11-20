import json

from app.db import connect, fixtures_dir

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS visitors (
  id TEXT PRIMARY KEY,
  label TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  visitor_id TEXT NOT NULL REFERENCES visitors(id),
  started_at TEXT NOT NULL,
  ended_at TEXT,
  hit_count INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'open'
);

CREATE TABLE IF NOT EXISTS hits (
  id TEXT PRIMARY KEY,
  visitor_id TEXT NOT NULL REFERENCES visitors(id),
  session_id TEXT REFERENCES sessions(id),
  event_ts TEXT NOT NULL,
  page_path TEXT NOT NULL,
  event_name TEXT NOT NULL,
  channel TEXT NOT NULL DEFAULT 'primary',
  UNIQUE(visitor_id, event_ts, page_path, event_name)
);

CREATE TABLE IF NOT EXISTS quarantine (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  visitor_id TEXT,
  reason TEXT NOT NULL,
  member_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'held',
  release_batch_id TEXT,
  released_at TEXT
);

CREATE TABLE IF NOT EXISTS ingest_batches (
  id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  report_json TEXT NOT NULL DEFAULT '{}'
);

-- 放行批次：一次放行动作一行（不可变，仅 INSERT）
CREATE TABLE IF NOT EXISTS hold_release_batches (
  id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  requested_hold_ids_json TEXT NOT NULL,
  entry_count INTEGER NOT NULL,
  result TEXT NOT NULL,
  report_json TEXT NOT NULL DEFAULT '{}'
);

-- 独立放行重裁边表：每个搁置成员一行，记录重裁前后会话与切开结论（不可变，仅 INSERT）
CREATE TABLE IF NOT EXISTS hold_release_reentries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  release_batch_id TEXT NOT NULL,
  hold_id INTEGER NOT NULL,
  hold_key TEXT NOT NULL,
  visitor_id TEXT NOT NULL,
  trigger_hit_id TEXT NOT NULL,
  old_session_id TEXT,
  new_session_id TEXT NOT NULL,
  split INTEGER NOT NULL,
  gate_decisions_json TEXT NOT NULL,
  outcome TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS session_proofs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  visitor_id TEXT NOT NULL,
  hit_ids_json TEXT NOT NULL,
  hit_count INTEGER NOT NULL,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  created_at TEXT NOT NULL
);
"""

# 既有库的增量迁移（新库由 SCHEMA 直接建成）
_QUARANTINE_MIGRATIONS = [
    ("status", "ALTER TABLE quarantine ADD COLUMN status TEXT NOT NULL DEFAULT 'held'"),
    ("release_batch_id", "ALTER TABLE quarantine ADD COLUMN release_batch_id TEXT"),
    ("released_at", "ALTER TABLE quarantine ADD COLUMN released_at TEXT"),
]


def _migrate(conn):
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(quarantine)").fetchall()}
    for col, ddl in _QUARANTINE_MIGRATIONS:
        if col not in cols:
            conn.execute(ddl)


def init_db():
    conn = connect()
    conn.executescript(SCHEMA)
    _migrate(conn)
    row = conn.execute("SELECT value FROM settings WHERE key='idle_timeout_sec'").fetchone()
    if not row:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES ('idle_timeout_sec', ?)",
            ("1800",),
        )
    n = conn.execute("SELECT COUNT(*) AS c FROM visitors").fetchone()["c"]
    if n == 0:
        _seed_fixtures(conn)
    conn.commit()
    conn.close()


def _seed_fixtures(conn):
    fixture = fixtures_dir() / "seed_hits.json"
    if not fixture.exists():
        return
    data = json.loads(fixture.read_text(encoding="utf-8"))
    for v in data.get("visitors", []):
        conn.execute(
            "INSERT INTO visitors(id, label, created_at) VALUES (?,?,?)",
            (v["id"], v["label"], v["created_at"]),
        )
    # 基座：按访客整段落进一个 open 会话（超时切分留给功能题空桩）
    for visitor_id, group in _group_hits(data.get("hits", [])).items():
        group = sorted(group, key=lambda h: (h["event_ts"], h["id"]))
        sid = f"S-{visitor_id}-1"
        conn.execute(
            "INSERT INTO sessions(id, visitor_id, started_at, ended_at, hit_count, status) VALUES (?,?,?,?,?,?)",
            (
                sid,
                visitor_id,
                group[0]["event_ts"],
                group[-1]["event_ts"],
                len(group),
                "open",
            ),
        )
        hit_ids = []
        for h in group:
            conn.execute(
                """
                INSERT INTO hits(id, visitor_id, session_id, event_ts, page_path, event_name, channel)
                VALUES (?,?,?,?,?,?,?)
                """,
                (
                    h["id"],
                    visitor_id,
                    sid,
                    h["event_ts"],
                    h["page_path"],
                    h["event_name"],
                    h.get("channel", "primary"),
                ),
            )
            hit_ids.append(h["id"])
        conn.execute(
            """
            INSERT INTO session_proofs(session_id, visitor_id, hit_ids_json, hit_count, started_at, ended_at, created_at)
            VALUES (?,?,?,?,?,?,datetime('now'))
            """,
            (
                sid,
                visitor_id,
                json.dumps(hit_ids, ensure_ascii=False),
                len(hit_ids),
                group[0]["event_ts"],
                group[-1]["event_ts"],
            ),
        )


def _group_hits(hits):
    out = {}
    for h in hits:
        out.setdefault(h["visitor_id"], []).append(h)
    return out


def idle_timeout_sec(conn) -> int:
    row = conn.execute("SELECT value FROM settings WHERE key='idle_timeout_sec'").fetchone()
    return int(row["value"]) if row else 1800
