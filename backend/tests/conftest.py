"""pytest 公共夹具：每个用例一个独立临时 SQLite 库（不碰本地 data/）。"""
import json
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

FIXTURES = BACKEND.parent / "fixtures"


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("FIXTURES_DIR", str(tmp_path / "fixtures"))

    from app import seed
    from app.db import connect

    seed.init_db()
    yield connect


def load_scenario(name: str = "hold_release_reentry.json") -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def seed_baseline(conn, scenario: dict):
    """按夹具造基线访客 + open 会话（含正式成员与核证）。"""
    from app.services.hold import _now

    for block in scenario["baseline_sessions"]:
        vid = block["visitor_id"]
        conn.execute(
            "INSERT OR IGNORE INTO visitors(id, label, created_at) VALUES (?,?,?)",
            (vid, block["visitor_label"], _now()),
        )
        hits = block.get("hits") or []
        if not hits:
            continue
        sid = block["session_id"]
        conn.execute(
            "INSERT INTO sessions(id, visitor_id, started_at, ended_at, hit_count, status) VALUES (?,?,?,?,?,?)",
            (sid, vid, hits[0]["event_ts"], hits[-1]["event_ts"], len(hits), "open"),
        )
        ids = []
        for h in hits:
            conn.execute(
                "INSERT INTO hits(id, visitor_id, session_id, event_ts, page_path, event_name, channel) VALUES (?,?,?,?,?,?,'primary')",
                (h["id"], vid, sid, h["event_ts"], h["page_path"], h["event_name"]),
            )
            ids.append(h["id"])
        conn.execute(
            "INSERT INTO session_proofs(session_id, visitor_id, hit_ids_json, hit_count, started_at, ended_at, created_at) VALUES (?,?,?,?,?,?,?)",
            (sid, vid, json.dumps(ids), len(ids), hits[0]["event_ts"], hits[-1]["event_ts"], _now()),
        )
    conn.commit()


def seed_holds(conn, scenario: dict) -> dict:
    """按夹具把 holds 全部收进搁置区，返回 case -> hold_id 映射。"""
    from app.services import hold as hold_svc

    conn.execute("BEGIN IMMEDIATE")
    mapping = {}
    for item in scenario["holds"]:
        out = hold_svc.hold_hit(item["hit"], item["reason"], conn=conn)
        mapping[item["case"]] = out["hold_id"]
    conn.commit()
    return mapping
