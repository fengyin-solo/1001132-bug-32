"""边坡移交域：班组、权限快照、会签、巡检、工单、待办的表结构与引导数据。

设计围绕“权限快照（authority snapshot）”：

- 边坡台账的每条记录都带一个在某时刻签发的权限快照，快照指向当时的责任班组；
- 会话凭据（token）属于某次快照签发，快照一旦被移交轮换，旧 token 立即失效；
- 移交必须双方会签，生效时间以双方签署记录为准（双方都签署的时刻）；
- 历史巡检按提交时的快照归属原班组，移交不改变历史归属；
- 无归属边坡不直接挂到接收队，先进入待认领池。

所有表都挂在内存 Store 上，键名集中在本模块定义，避免散落在各层。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.store import store


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# —— 表名 ——
T_TEAMS = "slope_team"            # 养护班组
T_SNAPSHOTS = "slope_authority"   # 权限快照（每次移交/签发产生一个版本）
T_SESSIONS = "slope_session"      # 班组会话凭据
T_TRANSFERS = "slope_transfer"    # 移交单（会签记录）
T_INSPECTIONS = "slope_inspection"  # 巡检/检查提交
T_WORK_ORDERS = "slope_work_order"  # 整改工单
T_TODOS = "slope_todo"            # 班组待办

CLAIM_POOL_TEAM = "待认领池"  # 无归属边坡的临时落点
SYSTEM_TEAM = "平台调度"      # 初始快照签发方

# 移交单状态
DRAFT = "待会签"        # 已发起，等待双方签署
SIGNED = "已生效"       # 双方签署完成、快照切换
REJECTED = "已驳回"     # 任一方拒签
VOID = "已作废"         # 被新的签署版本取代（并发竞争失败方）

# 工单状态
ORDER_OPEN = "待整改"
ORDER_DONE = "已整改"


class DomainError(Exception):
    """移交域业务规则被违反（越权、版本冲突、凭据失效等）。"""


def bootstrap() -> None:
    """首次使用时给三个示例边坡补齐班组与初始权限快照。

    边坡 1、2 归甲班，边坡 3 归乙班；另留一条无归属边坡用于演示待认领池。
    幂等：已引导过则直接返回。
    """
    teams = store.ensure_table(T_TEAMS)
    if teams:
        return
    teams.extend([
        {"id": 1, "name": "甲养护队", "code": "TEAM-A"},
        {"id": 2, "name": "乙养护队", "code": "TEAM-B"},
        {"id": 3, "name": CLAIM_POOL_TEAM, "code": "CLAIM-POOL"},
    ])

    slopes = store.rows("slope")
    # 给示例数据补上真实一点的编号与归属基础字段，便于演示；归属本身存在快照里。
    owner_plan = {1: "TEAM-A", 2: "TEAM-A", 3: "TEAM-B"}
    for row in slopes:
        sid = int(row["id"])
        code = owner_plan.get(sid)
        row["归属状态"] = "已认领" if code else "待认领"
        row["责任班组"] = _team_name(code) if code else CLAIM_POOL_TEAM
        row["当前快照版本"] = None  # 签发后回填
        if code:
            _issue_snapshot(sid, code, reason="建账初始签发", actor=SYSTEM_TEAM)
        else:
            row["归属状态"] = "待认领"
            row["责任班组"] = CLAIM_POOL_TEAM

    # 额外构造一条无归属、无快照的迁移边坡，验证“先进入待认领池”。
    orphan = {
        "id": store.next_id("slope"),
        "status": "局部变形",
        "pending": True,
        "abnormal": True,
        "边坡编号": "SLOP-0004",
        "所属路段": "北三环 K3+200",
        "边坡类型": "路堑边坡",
        "坡高": "14m",
        "防护形式": "锚杆框架",
        "稳定性评级": "二类",
        "最近巡检": "—",
        "边坡状态": "待移交",
        "归属状态": "待认领",
        "责任班组": CLAIM_POOL_TEAM,
        "当前快照版本": None,
    }
    store.rows("slope").append(orphan)


def _team_name(code: str | None) -> str | None:
    if code is None:
        return None
    for team in store.rows(T_TEAMS):
        if team["code"] == code:
            return str(team["name"])
    return code


def _issue_snapshot(
    slope_id: int,
    team_code: str | None,
    *,
    reason: str,
    actor: str,
    transfer_id: int | None = None,
    issued_at: str | None = None,
) -> dict[str, Any]:
    """签发一个新的权限快照版本并把边坡台账指向它。返回新快照。"""
    snapshots = store.rows(T_SNAPSHOTS)
    previous = [s for s in snapshots if int(s["slope_id"]) == slope_id]
    version = (max((int(s["version"]) for s in previous), default=0) + 1) if previous else 1
    for old in previous:
        old["active"] = False  # 同一边坡同一时刻只有一个生效快照
    snapshot = {
        "id": store.next_id(T_SNAPSHOTS),
        "slope_id": slope_id,
        "version": version,
        "team_code": team_code,
        "team_name": _team_name(team_code),
        "reason": reason,
        "actor": actor,
        "transfer_id": transfer_id,
        "issued_at": issued_at or now_text(),
        "active": True,
    }
    snapshots.append(snapshot)

    slope = store.find("slope", slope_id)
    if slope is not None:
        slope["当前快照版本"] = version
        if team_code is None:
            slope["责任班组"] = CLAIM_POOL_TEAM
            slope["归属状态"] = "待认领"
        else:
            slope["责任班组"] = snapshot["team_name"]
            slope["归属状态"] = "已认领"
    return snapshot


# 引导函数之外，供 service 层复用的内部工具再导出。
issue_snapshot = _issue_snapshot
team_name = _team_name
