"""搁置放行必须重裁（hold_release_reentry 功能题）。

对外定死：从搁置回到正式通道必须重新进入收流裁决
（app.services.adjudication 的门闸注册表，至少含空闲切窗
idle_timeout_cut；着陆/活动码等切开闸实现后在注册表登记即一并参与），
禁止「放行 = 直写成员 / 直改末击点」。

落库形态（二选一之「独立放行重裁边表」）：
  hold_release_batches          放行批次（不可变）
  hold_release_reentries        每搁置成员的重裁边表：搁置键、放行批次、
                                裁决后旧/新会话、是否切开、触发 hit、门闸轨迹
  quarantine.status/release_*   仅作搁置态，不是唯一持久手段

失败闭（二选一之「整批同事务」）：出搁置 + 重裁 + 成员改挂 + 审计在
同一个 BEGIN IMMEDIATE 事务内提交，任一失败整体回滚。

核证只读：GET /api/reentries、GET /api/release-batches。
"""
from app.services import hold as hold_service
from app.services.adjudication import GATES, adjudicate, materialize

STATUS = "implemented"

# 已注册参与放行重裁的切开闸（当前基座仅空闲切窗生效）
ACTIVE_GATES = tuple(g.__name__ for g in GATES)

__all__ = ["STATUS", "ACTIVE_GATES", "adjudicate", "materialize", "hold_service"]
