from fastapi import APIRouter, HTTPException

from app.db import connect
from app.seed import idle_timeout_sec
from app.services import hold as hold_svc
from app.services import ingest as ingest_svc
from app.services import sessions as sess_svc

router = APIRouter(prefix="/api")


@router.get("/overview")
def overview():
    conn = connect()
    try:
        return {
            "visitors": conn.execute("SELECT COUNT(*) AS c FROM visitors").fetchone()["c"],
            "sessions": conn.execute("SELECT COUNT(*) AS c FROM sessions").fetchone()["c"],
            "hits_primary": conn.execute(
                "SELECT COUNT(*) AS c FROM hits WHERE channel='primary'"
            ).fetchone()["c"],
            "quarantine": conn.execute("SELECT COUNT(*) AS c FROM quarantine").fetchone()["c"],
            "idle_timeout_sec": idle_timeout_sec(conn),
        }
    finally:
        conn.close()


@router.get("/visitors")
def list_visitors():
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT id, label, created_at FROM visitors ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.get("/sessions")
def list_sessions(visitor_id: str | None = None):
    conn = connect()
    try:
        return sess_svc.list_sessions(conn, visitor_id)
    finally:
        conn.close()


@router.get("/sessions/{session_id}")
def get_session(session_id: str):
    conn = connect()
    try:
        detail = sess_svc.session_detail(conn, session_id)
        if not detail:
            raise HTTPException(404, "会话不存在")
        return detail
    finally:
        conn.close()


@router.get("/quarantine")
def list_quarantine(status: str | None = None):
    conn = connect()
    try:
        return hold_svc.list_holds(conn, status)
    finally:
        conn.close()


@router.post("/quarantine/hold")
def post_hold(body: dict):
    """搁置入口：把一个击点收进搁置区（夹具/手工录入共用）。"""
    conn = connect()
    try:
        out = hold_svc.hold_hit(body.get("hit") or body, body.get("reason", "manual_hold"), conn)
        conn.commit()
        return out
    except hold_svc.ReleaseError as e:
        conn.rollback()
        raise HTTPException(400, str(e))
    finally:
        conn.close()


@router.post("/quarantine/release")
def post_release(body: dict):
    """放行：重新进入收流裁决并同事务物化，禁止直写成员。"""
    hold_ids = body.get("hold_ids")
    if not isinstance(hold_ids, list) or not hold_ids:
        raise HTTPException(400, "hold_ids 必须是非空数组")
    try:
        hold_ids = [int(x) for x in hold_ids]
    except (TypeError, ValueError):
        raise HTTPException(400, "hold_ids 必须是整数数组")
    result = hold_svc.release(hold_ids)
    if not result.get("ok"):
        raise HTTPException(409, result["error"])
    return result


@router.get("/reentries")
def get_reentries(batch_id: str | None = None, hold_id: int | None = None):
    """放行重裁核证（只读边表）。"""
    conn = connect()
    try:
        return hold_svc.list_reentries(conn, batch_id, hold_id)
    finally:
        conn.close()


@router.get("/release-batches")
def get_release_batches():
    """放行批次台账（只读）。"""
    conn = connect()
    try:
        return hold_svc.list_batches(conn)
    finally:
        conn.close()


@router.post("/ingest/run")
def run_ingest():
    return ingest_svc.run_batch()


@router.get("/session-proofs")
def session_proofs(session_id: str | None = None):
    conn = connect()
    try:
        if session_id:
            rows = conn.execute(
                "SELECT * FROM session_proofs WHERE session_id=? ORDER BY id DESC",
                (session_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM session_proofs ORDER BY id DESC LIMIT 50"
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.get("/settings")
def get_settings():
    conn = connect()
    try:
        return {"idle_timeout_sec": idle_timeout_sec(conn)}
    finally:
        conn.close()


@router.put("/settings/idle-timeout")
def put_idle_timeout(body: dict):
    sec = int(body.get("idle_timeout_sec", 1800))
    if sec < 60:
        raise HTTPException(400, "空闲超时至少 60 秒")
    conn = connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO settings(key, value) VALUES ('idle_timeout_sec', ?)",
            (str(sec),),
        )
        conn.commit()
        return {"idle_timeout_sec": sec}
    finally:
        conn.close()
