"""hold_release_reentry 功能题测试：本地夹具 + 临时 SQLite。

对照两类放行结果：
  - 应切开另起 open（超空闲窗 / 无 open）
  - 应并入原 open（空闲窗内）
并验证失败闭、禁直写、只读核证、批次/边表持久化。
"""
import json

import pytest

from conftest import load_scenario, seed_baseline, seed_holds


@pytest.fixture()
def scenario_db(temp_db):
    conn = temp_db()
    scenario = load_scenario()
    seed_baseline(conn, scenario)
    hold_map = seed_holds(conn, scenario)
    conn.commit()
    return conn, scenario, hold_map


def _primary_hit(conn, hit_id):
    return conn.execute(
        "SELECT * FROM hits WHERE id=? AND channel='primary'", (hit_id,)
    ).fetchone()


def _proof_ids(conn, session_id):
    row = conn.execute(
        "SELECT hit_ids_json FROM session_proofs WHERE session_id=? ORDER BY id DESC LIMIT 1",
        (session_id,),
    ).fetchone()
    return json.loads(row["hit_ids_json"]) if row else None


# ---- 放行两结果：切开 / 并入 -------------------------------------------

def test_release_split_when_idle_gap_exceeded(scenario_db):
    conn, scenario, hold_map = scenario_db
    from app.services import hold as hold_svc

    hold_id = hold_map["should_split_idle_gap"]
    res = hold_svc.release([hold_id])
    assert res["ok"] is True
    assert res["split_count"] == 1

    entry = res["reentries"][0]
    assert entry["split"] is True
    assert entry["outcome"] == "split_new_open"
    assert entry["old_session_id"] == "S-V-2001-base"
    assert entry["new_session_id"] != "S-V-2001-base"
    assert entry["trigger_hit_id"] == "HQ1"

    # 旧 open 收口，新 open 承接
    old = conn.execute("SELECT status FROM sessions WHERE id='S-V-2001-base'").fetchone()
    assert old["status"] == "closed"
    new = conn.execute("SELECT * FROM sessions WHERE id=?", (entry["new_session_id"],)).fetchone()
    assert new["status"] == "open" and new["hit_count"] == 1

    # 正式成员落在新会话，核证同源
    hit = _primary_hit(conn, "HQ1")
    assert hit["session_id"] == entry["new_session_id"]
    assert _proof_ids(conn, entry["new_session_id"]) == ["HQ1"]
    assert "HQ1" not in _proof_ids(conn, "S-V-2001-base")

    # 门闸轨迹记录了空闲切窗依据
    gate = entry["gate_decisions"][0]
    assert gate["gate"] == "idle_timeout_cut"
    assert gate["decision"] == "split"
    assert gate["gap_sec"] == pytest.approx(3300.0)  # 09:05 → 10:00


def test_release_join_when_within_idle_window(scenario_db):
    conn, scenario, hold_map = scenario_db
    from app.services import hold as hold_svc

    hold_id = hold_map["should_join_within_window"]
    res = hold_svc.release([hold_id])
    assert res["ok"] is True
    assert res["join_count"] == 1

    entry = res["reentries"][0]
    assert entry["split"] is False
    assert entry["outcome"] == "join_existing_open"
    assert entry["new_session_id"] == "S-V-2002-base"
    assert entry["old_session_id"] == "S-V-2002-base"

    sess = conn.execute("SELECT * FROM sessions WHERE id='S-V-2002-base'").fetchone()
    assert sess["status"] == "open" and sess["hit_count"] == 3
    assert _primary_hit(conn, "HQ2")["session_id"] == "S-V-2002-base"
    assert _proof_ids(conn, "S-V-2002-base") == ["HB3", "HB4", "HQ2"]

    gate = entry["gate_decisions"][0]
    assert gate["decision"] == "continue"
    assert gate["gap_sec"] == pytest.approx(120.0)  # 10:08 → 10:10


def test_release_split_when_no_open_session(scenario_db):
    conn, scenario, hold_map = scenario_db
    from app.services import hold as hold_svc

    res = hold_svc.release([hold_map["should_split_no_open_session"]])
    assert res["ok"] is True
    entry = res["reentries"][0]
    assert entry["split"] is True and entry["old_session_id"] is None
    new = conn.execute("SELECT * FROM sessions WHERE id=?", (entry["new_session_id"],)).fetchone()
    assert new["visitor_id"] == "V-2003" and new["status"] == "open"
    assert _proof_ids(conn, entry["new_session_id"]) == ["HQ3"]


# ---- 持久形态：独立边表 + 批次，不只改状态位 -----------------------------

def test_persistence_uses_edge_table_and_batch(scenario_db):
    conn, scenario, hold_map = scenario_db
    from app.services import hold as hold_svc

    res = hold_svc.release(
        [hold_map["should_split_idle_gap"], hold_map["should_join_within_window"]]
    )
    batch_id = res["batch_id"]

    batches = hold_svc.list_batches(conn)
    assert len(batches) == 1 and batches[0]["id"] == batch_id
    assert batches[0]["result"] == "committed"
    assert batches[0]["report"]["entry_count"] == 2
    assert batches[0]["requested_hold_ids"] == [
        hold_map["should_split_idle_gap"],
        hold_map["should_join_within_window"],
    ]

    reentries = hold_svc.list_reentries(conn)
    assert len(reentries) == 2
    by_hold = {r["hold_id"]: r for r in reentries}
    split_row = by_hold[hold_map["should_split_idle_gap"]]
    assert split_row["hold_key"] == f"Q{hold_map['should_split_idle_gap']}"
    assert split_row["release_batch_id"] == batch_id
    assert split_row["split"] is True
    assert split_row["trigger_hit_id"] == "HQ1"
    assert split_row["old_session_id"] == "S-V-2001-base"
    assert split_row["new_session_id"] != split_row["old_session_id"]
    assert split_row["gate_decisions"][0]["gate"] == "idle_timeout_cut"

    # 搁置态也翻转，但边表才是重裁核证主据
    q = conn.execute("SELECT * FROM quarantine WHERE id=?", (split_row["hold_id"],)).fetchone()
    assert q["status"] == "released" and q["release_batch_id"] == batch_id and q["released_at"]


def test_reentry_filter_by_hold_and_batch(scenario_db):
    conn, scenario, hold_map = scenario_db
    from app.services import hold as hold_svc

    r1 = hold_svc.release([hold_map["should_split_idle_gap"]])
    r2 = hold_svc.release([hold_map["should_join_within_window"]])
    assert len(hold_svc.list_reentries(conn, batch_id=r1["batch_id"])) == 1
    only = hold_svc.list_reentries(conn, hold_id=hold_map["should_join_within_window"])
    assert len(only) == 1 and only[0]["release_batch_id"] == r2["batch_id"]


# ---- 失败闭：同事务整体回滚 ---------------------------------------------

def test_failure_rolls_back_half_released_batch(scenario_db, monkeypatch):
    conn, scenario, hold_map = scenario_db
    from app.services import hold as hold_svc
    from app.services import adjudication

    h_split = hold_map["should_split_idle_gap"]
    h_join = hold_map["should_join_within_window"]

    calls = {"n": 0}
    real = adjudication.materialize

    def flaky(conn_, hit, verdict):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("injected materialize failure")
        return real(conn_, hit, verdict)

    monkeypatch.setattr(hold_svc.adjudication, "materialize", flaky)

    res = hold_svc.release([h_split, h_join])
    assert res["ok"] is False
    assert "injected materialize failure" in res["error"]

    # 第一条已物化的内容随事务回滚：无正式成员、无新会话、搁置仍 held
    assert _primary_hit(conn, "HQ1") is None
    assert _primary_hit(conn, "HQ2") is None
    assert conn.execute("SELECT COUNT(*) c FROM sessions WHERE id LIKE 'S-V-2001-R%'").fetchone()["c"] == 0
    held = conn.execute("SELECT status FROM quarantine WHERE id=?", (h_split,)).fetchone()
    assert held["status"] == "held"
    assert conn.execute("SELECT COUNT(*) c FROM hold_release_reentries").fetchone()["c"] == 0
    assert conn.execute("SELECT COUNT(*) c FROM hold_release_batches").fetchone()["c"] == 0
    # 基线未受污染
    assert conn.execute("SELECT hit_count FROM sessions WHERE id='S-V-2001-base'").fetchone()["hit_count"] == 2


def test_release_unknown_or_non_held_is_rejected(scenario_db):
    conn, scenario, hold_map = scenario_db
    from app.services import hold as hold_svc

    ok = hold_svc.release([hold_map["should_join_within_window"]])
    assert ok["ok"] is True
    # 重复放行已 released 的记录：拒绝且无副作用
    again = hold_svc.release([hold_map["should_join_within_window"]])
    assert again["ok"] is False and "不可放行" in again["error"]
    # 不存在的 id
    missing = hold_svc.release([99999])
    assert missing["ok"] is False and "不存在" in missing["error"]
    # 空批次
    empty = hold_svc.release([])
    assert empty["ok"] is False


def test_idempotent_reject_when_hit_already_primary(scenario_db):
    conn, scenario, hold_map = scenario_db
    from app.services import hold as hold_svc

    # 先把同 id 击点直接放进正式通道（模拟重放/双占），放行必须拒绝
    conn.execute(
        "INSERT INTO hits(id, visitor_id, session_id, event_ts, page_path, event_name, channel) "
        "VALUES ('HQ1','V-2001','S-V-2001-base','2026-11-18T10:00:00Z','/x','view','primary')"
    )
    conn.commit()
    res = hold_svc.release([hold_map["should_split_idle_gap"]])
    assert res["ok"] is False and "幂等" in res["error"]
    held = conn.execute("SELECT status FROM quarantine WHERE id=?", (hold_map["should_split_idle_gap"],)).fetchone()
    assert held["status"] == "held"
    assert conn.execute("SELECT COUNT(*) c FROM hold_release_reentries").fetchone()["c"] == 0


# ---- 禁直写：只有裁决-物化口能改挂成员 -----------------------------------

def test_no_direct_write_bypass_path_exists_only_via_adjudication(scenario_db):
    """模块对外只暴露 adjudicate -> materialize 路径；hold 服务不出现直写 hits 的放行分支。"""
    import inspect

    from app.services import hold as hold_svc

    src = inspect.getsource(hold_svc)
    # 放行函数内不得 INSERT/UPDATE hits 或 sessions（成员改挂只允许在 adjudication.materialize）
    release_src = inspect.getsource(hold_svc.release)
    assert "INSERT INTO hits" not in release_src
    assert "UPDATE sessions" not in release_src
    assert "adjudication.adjudicate" in src and "adjudication.materialize" in src


def test_hold_hit_requires_fields(temp_db):
    conn = temp_db()
    from app.services import hold as hold_svc

    with pytest.raises(hold_svc.ReleaseError):
        hold_svc.hold_hit({"id": "X", "visitor_id": "V1"}, "dirty_hit_hold", conn=conn)
    assert conn.execute("SELECT COUNT(*) c FROM quarantine").fetchone()["c"] == 0


# ---- 核证只读：接口不落库 -----------------------------------------------

def test_read_only_certification_apis(scenario_db):
    conn, scenario, hold_map = scenario_db
    from app.services import hold as hold_svc

    hold_svc.release([hold_map["should_split_idle_gap"]])
    before = conn.execute("SELECT COUNT(*) c FROM hold_release_reentries").fetchone()["c"]
    # 只读接口连调不产生任何写入
    hold_svc.list_reentries(conn)
    hold_svc.list_batches(conn)
    after = conn.execute("SELECT COUNT(*) c FROM hold_release_reentries").fetchone()["c"]
    assert before == after == 1


# ---- HTTP 入口（TestClient）---------------------------------------------

def test_http_release_and_certification_endpoints(scenario_db):
    from fastapi.testclient import TestClient

    from app.main import app

    conn, scenario, hold_map = scenario_db
    client = TestClient(app)

    r = client.post(
        "/api/quarantine/release",
        json={"hold_ids": [hold_map["should_split_idle_gap"], hold_map["should_join_within_window"]]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["split_count"] == 1 and body["join_count"] == 1

    rows = client.get("/api/reentries").json()
    assert len(rows) == 2
    assert all("gate_decisions" in r for r in rows)
    batches = client.get("/api/release-batches").json()
    assert len(batches) == 1 and batches[0]["report"]["entry_count"] == 2

    # 搁置列表带状态
    q = client.get("/api/quarantine").json()
    released = [x for x in q if x["status"] == "released"]
    assert len(released) == 2

    # 冲突请求返回 409 且库不变
    dup = client.post("/api/quarantine/release", json={"hold_ids": [hold_map["should_split_idle_gap"]]})
    assert dup.status_code == 409
    bad = client.post("/api/quarantine/release", json={"hold_ids": []})
    assert bad.status_code == 400


def test_http_hold_entry(scenario_db):
    from fastapi.testclient import TestClient

    from app.main import app

    conn, scenario, hold_map = scenario_db
    client = TestClient(app)
    payload = {
        "reason": "dirty_hit_hold",
        "hit": {
            "id": "HQ9",
            "visitor_id": "V-2002",
            "event_ts": "2026-11-18T10:12:00Z",
            "page_path": "/pay",
            "event_name": "click",
            "visitor_label": "访客乙",
        },
    }
    r = client.post("/api/quarantine/hold", json=payload)
    assert r.status_code == 200 and r.json()["status"] == "held"

    res = client.post("/api/quarantine/release", json={"hold_ids": [r.json()["hold_id"]]})
    assert res.status_code == 200 and res.json()["join_count"] == 1
    assert _primary_hit(conn, "HQ9")["session_id"] == "S-V-2002-base"


# ---- 模块本体状态 --------------------------------------------------------

def test_module_status_implemented():
    from app.modules.hold_release_reentry import ACTIVE_GATES, STATUS

    assert STATUS == "implemented"
    assert "gate_idle_timeout" in ACTIVE_GATES
