"""边坡防护业务规则：状态流转、字段校验与筛选口径都收在这里。

边坡“责任班组”的唯一事实来源是权限快照（handover 域），列表与详情都通过同一
序列化口径读取，避免列表和详情归属不一致。
"""
from __future__ import annotations

from typing import Any

from app.services.handover import handover_service
from app.store import store

MODULE = "slope"
REQUIRED_FIELDS = ["边坡编号", "所属路段", "边坡类型"]
STATUS_ORDER = ["稳定", "局部变形", "失稳", "加固中"]
ACTION_RULES = {"登记变形": "局部变形", "安排加固": "加固中", "加固完成": "稳定"}
NEGATIVE_ACTIONS = []


class SlopeService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        team_code: str | None = None,
        pending_claim: bool = False,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        # 归属、待整改工单数统一由 handover 域按当前生效快照补齐
        return handover_service.list_slopes(
            keyword=keyword,
            status=status,
            team_code=team_code,
            pending_claim=pending_claim,
            page=page,
            size=size,
        )

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        row = store.find(MODULE, entry_id)
        if row is None:
            return None
        return handover_service._serialize_slope(row)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        # 新建边坡无归属，先进待认领池，不直接挂给任何班组
        entry["归属状态"] = "待认领"
        entry["责任班组"] = "待认领池"
        entry["当前快照版本"] = None
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"边坡 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于边坡防护可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return handover_service._serialize_slope(entry), f"边坡已{action}"
