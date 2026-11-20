"""回归：旧库增量迁移 + 基座收流/会话物化在新路径解析下保持可用。"""
import json


def test_migration_from_legacy_schema(tmp_path, monkeypatch):
    """旧库（quarantine 无状态列、无放行边表）启动后自动迁移且放行流程可用。"""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("FIXTURES_DIR", str(tmp_path / "fixtures"))

    from app.db import connect

    conn = connect()
    conn.executescript(
        """
        CREATE TABLE visitors (id TEXT PRIMARY KEY, label TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE sessions (
          id TEXT PRIMARY KEY, visitor_id TEXT NOT NULL, started_at TEXT NOT NULL,
          ended_at TEXT, hit_count INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'open'
        );
        CREATE TABLE hits (
          id TEXT PRIMARY KEY, visitor_id TEXT NOT NULL, session_id TEXT,
          event_ts TEXT NOT NULL, page_path TEXT NOT NULL, event_name TEXT NOT NULL,
          channel TEXT NOT NULL DEFAULT 'primary',
          UNIQUE(visitor_id, event_ts, page_path, event_name)
        );
        CREATE TABLE quarantine (
          id INTEGER PRIMARY KEY AUTOINCREMENT, visitor_id TEXT, reason TEXT NOT NULL,
          member_json TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE ingest_batches (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, report_json TEXT NOT NULL DEFAULT '{}');
        CREATE TABLE session_proofs (
          id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, visitor_id TEXT NOT NULL,
          hit_ids_json TEXT NOT NULL, hit_count INTEGER NOT NULL,
          started_at TEXT NOT NULL, ended_at TEXT, created_at TEXT NOT NULL
        );
        INSERT INTO visitors VALUES ('V-9001','旧客','2026-11-18T08:00:00Z');
        INSERT INTO sessions VALUES ('S-V-9001-base','V-9001','2026-11-18T09:00:00Z','2026-11-18T09:05:00Z',2,'open');
        INSERT INTO hits VALUES ('HB1','V-9001','S-V-9001-base','2026-11-18T09:00:00Z','/home','view','primary');
        INSERT INTO hits VALUES ('HB2','V-9001','S-V-9001-base','2026-11-18T09:05:00Z','/item','click','primary');
        """
    )
    conn.commit()
    conn.close()

    from app import seed
    from app.db import connect
    from app.services import hold as hold_svc

    seed.init_db()  # 触发增量迁移，不应报错

    conn = connect()
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(quarantine)").fetchall()}
    assert {"status", "release_batch_id", "released_at"} <= cols
    assert conn.execute(
        "SELECT name FROM sqlite_master WHERE name='hold_release_reentries'"
    ).fetchone()

    # 旧数据完好，且迁移后放行重裁可用（超空闲窗 → 切开）
    out = hold_svc.hold_hit(
        {"id": "HQ-legacy", "visitor_id": "V-9001", "event_ts": "2026-11-18T10:00:00Z",
         "page_path": "/checkout", "event_name": "view"},
        "dirty_hit_hold", conn=conn,
    )
    conn.commit()
    res = hold_svc.release([out["hold_id"]])
    assert res["ok"] is True and res["reentries"][0]["split"] is True
    conn.close()


def test_base_ingest_still_works(temp_db, tmp_path):
    """基座收流 run_batch 走 fixtures_dir() 动态路径，行为与改造前一致。"""
    fix_dir = tmp_path / "fixtures"
    fix_dir.mkdir()
    (fix_dir / "ingest_batch.json").write_text(
        json.dumps(
            {
                "batch_id": "B-smoke",
                "hits": [
                    {"id": "H6", "visitor_id": "V-1001", "event_ts": "2026-11-17T09:50:00Z",
                     "page_path": "/checkout", "event_name": "view", "visitor_label": "访客甲"},
                ],
            }
        ),
        encoding="utf-8",
    )
    import os
    os.environ["FIXTURES_DIR"] = str(fix_dir)

    from app.services.ingest import run_batch

    res = run_batch()
    assert res["ok"] is True and res["report"]["written"] == 1
    # 再跑一次：幂等去重，不重复计数
    again = run_batch()
    assert again["ok"] is True and again["report"]["written"] == 0
