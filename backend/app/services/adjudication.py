"""收流裁决：击点进入正式通道的统一裁决口。

对外定死的规则（hold_release_reentry 功能题）：
任何从搁置区回到正式通道的击点都必须重新进入本裁决，禁止
「放行 = 直写成员 / 直改末击点」。裁决由门闸注册表给出
切开/并入结论，再由物化器统一建会话 / 改挂成员 / 重出核证。

门闸注册表是基座切开闸的统一注册位：本轮 idle_timeout_cut
生效；landing_force_split / campaign_partition_split 等空桩
将来在此登记即可一并参与放行重裁。
"""
import json
import uuid
from datetime import datetime, timezone

from app.seed import idle_timeout_sec


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _gap_seconds(earlier_ts: str, later_ts: str) -> float:
    """两个 ISO8601(UTC, Z) 时刻的秒差；later 早于 earlier 时为负（迟到击点）。"""
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    a = datetime.strptime(earlier_ts, fmt)
    b = datetime.strptime(later_ts, fmt)
    return (b - a).total_seconds()


# ---- 门闸注册表 ---------------------------------------------------------
# 每个门闸：f(conn, hit, target) -> dict(decision, reason, ...)
# decision ∈ {'split', 'continue'}；任一注册门闸判 split 即切开。
# 未实现的基座空桩不在此登记（其 __init__.py 仍为 stub）。


def gate_idle_timeout(conn, hit: dict, target: dict | None) -> dict:
    """空闲超时切窗：距 open 末击点超过 idle_timeout_sec 即另起 open。"""
    timeout = idle_timeout_sec(conn)
    if target is None:
        return {
            "gate": "idle_timeout_cut",
            "decision": "split",
            "reason": "no_open_session",
            "idle_timeout_sec": timeout,
            "gap_sec": None,
        }
    gap = _gap_seconds(target["last_event_ts"], hit["event_ts"])
    if gap > timeout:
        return {
            "gate": "idle_timeout_cut",
            "decision": "split",
            "reason": "idle_gap_exceeded",
            "idle_timeout_sec": timeout,
            "gap_sec": gap,
        }
    return {
        "gate": "idle_timeout_cut",
        "decision": "continue",
        "reason": "within_idle_window",
        "idle_timeout_sec": timeout,
        "gap_sec": gap,
    }


# 登记顺序即裁决顺序；着陆/活动码等切开闸实现后在此追加。
GATES = (gate_idle_timeout,)


def find_open_target(conn, visitor_id: str) -> dict | None:
    """同访客当前唯一 open 会话及其末击点时刻（single_open_cap 基座约定）。"""
    row = conn.execute(
        """
        SELECT s.id, s.started_at, s.ended_at,
               (SELECT MAX(event_ts) FROM hits
                 WHERE session_id = s.id AND channel = 'primary') AS last_event_ts
        FROM sessions s
        WHERE s.visitor_id = ? AND s.status = 'open'
        ORDER BY s.started_at DESC
        LIMIT 1
        """,
        (visitor_id,),
    ).fetchone()
    return dict(row) if row else None


def adjudicate(conn, hit: dict, target: dict | None = None) -> dict:
    """纯读裁决：跑全部已注册门闸，给出 split/continue 结论与门闸轨迹。

    不写库。返回:
      {
        'split': bool, 'old_session_id': str|None,
        'gate_decisions': [...每闸明细...]
      }
    """
    if target is None:
        target = find_open_target(conn, hit["visitor_id"])
    decisions = [gate(conn, hit, target) for gate in GATES]
    split = any(d["decision"] == "split" for d in decisions)
    return {
        "split": split,
        "old_session_id": target["id"] if target else None,
        "gate_decisions": decisions,
    }


def _new_session_id(visitor_id: str) -> str:
    return f"S-{visitor_id}-R{uuid.uuid4().hex[:6]}"


def _rewrite_proof(conn, session_id: str):
    """按当前正式成员重出一条会话核证（核证只由物化器追加，不就地改旧条）。"""
    sess = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
    ids = [
        r["id"]
        for r in conn.execute(
            "SELECT id FROM hits WHERE session_id=? AND channel='primary' ORDER BY event_ts, id",
            (session_id,),
        ).fetchall()
    ]
    conn.execute(
        """
        INSERT INTO session_proofs(
          session_id, visitor_id, hit_ids_json, hit_count, started_at, ended_at, created_at
        ) VALUES (?,?,?,?,?,?,?)
        """,
        (
            session_id,
            sess["visitor_id"],
            json.dumps(ids, ensure_ascii=False),
            len(ids),
            sess["started_at"],
            sess["ended_at"],
            _now(),
        ),
    )


def materialize(conn, hit: dict, verdict: dict) -> str:
    """按裁决结论物化：建新 open 或并入既有 open，挂正式成员并重出核证。

    唯一允许改挂 hits / 建 sessions 的入口；调用方必须已通过 adjudicate。
    返回落点会话 id。
    """
    vid = hit["visitor_id"]
    old_sid = verdict["old_session_id"]
    if verdict["split"]:
        if old_sid is not None:
            # 切开：旧 open 收口为 closed，新事件另起 open
            conn.execute(
                "UPDATE sessions SET status='closed' WHERE id=? AND status='open'",
                (old_sid,),
            )
            _rewrite_proof(conn, old_sid)
        new_sid = _new_session_id(vid)
        conn.execute(
            """
            INSERT INTO sessions(id, visitor_id, started_at, ended_at, hit_count, status)
            VALUES (?,?,?,?,1,'open')
            """,
            (new_sid, vid, hit["event_ts"], hit["event_ts"]),
        )
        target_sid = new_sid
    else:
        target_sid = old_sid
        conn.execute(
            """
            UPDATE sessions
            SET hit_count = hit_count + 1,
                ended_at = CASE WHEN ended_at IS NULL OR ended_at < ? THEN ? ELSE ended_at END
            WHERE id=?
            """,
            (hit["event_ts"], hit["event_ts"], target_sid),
        )

    conn.execute(
        """
        INSERT INTO hits(id, visitor_id, session_id, event_ts, page_path, event_name, channel)
        VALUES (?,?,?,?,?,?,'primary')
        """,
        (
            hit["id"],
            vid,
            target_sid,
            hit["event_ts"],
            hit["page_path"],
            hit["event_name"],
        ),
    )
    _rewrite_proof(conn, target_sid)
    return target_sid
