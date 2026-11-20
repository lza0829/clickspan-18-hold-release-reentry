"""搁置区与放行重裁（hold_release_reentry 功能题）。

硬约束：
1. 放行不得直写成员 / 直改末击点，必须走 services.adjudication 的
   收流裁决（门闸注册表，至少含空闲切窗）。
2. 持久化采用「独立放行重裁边表」形态：hold_release_batches +
   hold_release_reentries，不只改 quarantine 状态位。
3. 失败闭：出搁置 + 重裁 + 成员改挂 + 审计在同一个 BEGIN IMMEDIATE
   事务内完成，任一环节失败整体回滚——不存在「放行成功但正式成员未变」
   或「半截出搁置」。
4. 重裁核证接口只读。
"""
import json
import uuid
from datetime import datetime, timezone

from app.db import connect
from app.services import adjudication


class ReleaseError(Exception):
    """放行前置校验失败（未出事务、无任何落库副作用时抛出）。"""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_batch_id() -> str:
    return f"REL-{uuid.uuid4().hex[:10]}"


# ---- 搁置入口 -----------------------------------------------------------

REQUIRED_HIT_FIELDS = ("id", "visitor_id", "event_ts", "page_path", "event_name")


def hold_hit(hit: dict, reason: str, conn=None) -> dict:
    """把一个击点放进搁置区（夹具与搁置入口共用）。

    自动补齐访客主数据；member_json 完整保存击点载荷，供放行重裁使用。
    可复用外部连接（测试/夹具），默认自管连接。
    """
    missing = [k for k in REQUIRED_HIT_FIELDS if not hit.get(k)]
    if missing:
        raise ReleaseError(f"搁置击点缺字段: {','.join(missing)}")

    own = conn is None
    if own:
        conn = connect()
    try:
        if own:
            conn.execute("BEGIN IMMEDIATE")
        if not conn.execute(
            "SELECT 1 FROM visitors WHERE id=?", (hit["visitor_id"],)
        ).fetchone():
            conn.execute(
                "INSERT INTO visitors(id, label, created_at) VALUES (?,?,?)",
                (hit["visitor_id"], hit.get("visitor_label") or hit["visitor_id"], _now()),
            )
        cur = conn.execute(
            """
            INSERT INTO quarantine(visitor_id, reason, member_json, created_at, status)
            VALUES (?,?,?,?,'held')
            """,
            (hit["visitor_id"], reason, json.dumps(hit, ensure_ascii=False), _now()),
        )
        hold_id = cur.lastrowid
        if own:
            conn.commit()
        return {"hold_id": hold_id, "hold_key": f"Q{hold_id}", "status": "held"}
    except Exception:
        if own:
            conn.rollback()
        raise
    finally:
        if own:
            conn.close()


def list_holds(conn, status: str | None = None):
    sql = "SELECT * FROM quarantine"
    params = ()
    if status:
        sql += " WHERE status=?"
        params = (status,)
    sql += " ORDER BY id"
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


# ---- 放行（同事务失败闭）------------------------------------------------

def _load_held(conn, hold_ids: list[int]) -> list[dict]:
    if not hold_ids:
        raise ReleaseError("未指定待放行搁置记录")
    rows = []
    seen = set()
    for hid in hold_ids:
        if hid in seen:
            raise ReleaseError(f"搁置记录重复: {hid}")
        seen.add(hid)
        row = conn.execute("SELECT * FROM quarantine WHERE id=?", (hid,)).fetchone()
        if row is None:
            raise ReleaseError(f"搁置记录不存在: {hid}")
        if row["status"] != "held":
            raise ReleaseError(
                f"搁置记录 {hid} 状态为 {row['status']}，不可放行（仅 held 可放行）"
            )
        rows.append(dict(row))
    return rows


def release(hold_ids: list[int]) -> dict:
    """放行一批搁置击点：整批一个事务，全部成功或整体回滚。

    流程（单事务）：
      锁行 → 前置校验（held/幂等）→ 逐条 重裁(只读) → 物化(建/并会话、
      挂正式成员、重出核证) → 写重裁边表 → 翻搁置态 → 写批次 → commit。
    """
    batch_id = _new_batch_id()
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        holds = _load_held(conn, hold_ids)

        reentries = []
        for hold in holds:
            hit = json.loads(hold["member_json"])
            # 幂等：同 hit 已是正式成员则拒绝，杜绝重放双占
            dup = conn.execute(
                "SELECT 1 FROM hits WHERE id=? AND channel='primary'", (hit["id"],)
            ).fetchone()
            if dup:
                raise ReleaseError(f"击点 {hit['id']} 已在正式通道，放行拒绝（幂等）")

            verdict = adjudication.adjudicate(conn, hit)
            new_sid = adjudication.materialize(conn, hit, verdict)

            outcome = "split_new_open" if verdict["split"] else "join_existing_open"
            conn.execute(
                """
                INSERT INTO hold_release_reentries(
                  release_batch_id, hold_id, hold_key, visitor_id, trigger_hit_id,
                  old_session_id, new_session_id, split, gate_decisions_json,
                  outcome, created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    batch_id,
                    hold["id"],
                    f"Q{hold['id']}",
                    hold["visitor_id"],
                    hit["id"],
                    verdict["old_session_id"],
                    new_sid,
                    1 if verdict["split"] else 0,
                    json.dumps(verdict["gate_decisions"], ensure_ascii=False),
                    outcome,
                    _now(),
                ),
            )
            conn.execute(
                """
                UPDATE quarantine
                SET status='released', release_batch_id=?, released_at=?
                WHERE id=? AND status='held'
                """,
                (batch_id, _now(), hold["id"]),
            )
            if conn.execute("SELECT changes()").fetchone()[0] != 1:
                # 并发兜底：状态在事务内被改动则整批回滚
                raise ReleaseError(f"搁置记录 {hold['id']} 状态翻转失败，整批回滚")
            reentries.append(
                {
                    "hold_id": hold["id"],
                    "hold_key": f"Q{hold['id']}",
                    "trigger_hit_id": hit["id"],
                    "old_session_id": verdict["old_session_id"],
                    "new_session_id": new_sid,
                    "split": verdict["split"],
                    "gate_decisions": verdict["gate_decisions"],
                    "outcome": outcome,
                }
            )

        report = {
            "batch_id": batch_id,
            "entry_count": len(reentries),
            "split_count": sum(1 for r in reentries if r["split"]),
            "join_count": sum(1 for r in reentries if not r["split"]),
        }
        conn.execute(
            """
            INSERT INTO hold_release_batches(
              id, created_at, requested_hold_ids_json, entry_count, result, report_json
            ) VALUES (?,?,?,?,?,?)
            """,
            (
                batch_id,
                _now(),
                json.dumps(hold_ids, ensure_ascii=False),
                len(hold_ids),
                "committed",
                json.dumps(report, ensure_ascii=False),
            ),
        )
        conn.commit()
        return {"ok": True, **report, "reentries": reentries}
    except Exception as e:
        conn.rollback()
        return {"ok": False, "error": str(e), "batch_id": batch_id}
    finally:
        conn.close()


# ---- 只读核证接口 -------------------------------------------------------

def list_reentries(conn, batch_id: str | None = None, hold_id: int | None = None):
    sql = "SELECT * FROM hold_release_reentries WHERE 1=1"
    params = []
    if batch_id:
        sql += " AND release_batch_id=?"
        params.append(batch_id)
    if hold_id is not None:
        sql += " AND hold_id=?"
        params.append(hold_id)
    sql += " ORDER BY id"
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    for r in rows:
        r["split"] = bool(r["split"])
        r["gate_decisions"] = json.loads(r.pop("gate_decisions_json"))
    return rows


def list_batches(conn):
    rows = conn.execute("SELECT * FROM hold_release_batches ORDER BY created_at DESC").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["report"] = json.loads(d.pop("report_json"))
        d["requested_hold_ids"] = json.loads(d.pop("requested_hold_ids_json"))
        out.append(d)
    return out
