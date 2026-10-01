"""边坡移交主服务：以“权限快照”为唯一归属依据，串联会话、巡检、工单、待办与移交。

所有写操作都在 ``store.transaction()`` 内完成，保证：

1. 会话签发 / 凭据失效、工单归属、台账快照、工程清单、班组待办要么一起提交，
   要么一起回滚，杜绝半提交；
2. 并发移交通过“基础快照版本 + 生效前复检”做乐观控制，同一边坡只接受一个签署
   版本，竞争失败的移交单作废；
3. 凭据随移交事务联动轮换，旧 token 立即失活，断网后重试也不能复活旧授权；
4. 历史巡检记录提交时的班组与快照版本，归属不随后续移交改变。
"""
from __future__ import annotations

import secrets
from typing import Any

from app.services.handover_domain import (
    CLAIM_POOL_TEAM,
    DRAFT,
    ORDER_DONE,
    ORDER_OPEN,
    REJECTED,
    SIGNED,
    SYSTEM_TEAM,
    T_INSPECTIONS,
    T_SESSIONS,
    T_SNAPSHOTS,
    T_TEAMS,
    T_TODOS,
    T_TRANSFERS,
    T_WORK_ORDERS,
    VOID,
    DomainError,
    issue_snapshot,
    now_text,
    team_name,
)
from app.store import store

POOL_CODE = "CLAIM-POOL"
PROJECT_PREFIX = "SL-"  # 边坡整改工程在工程清单里的编号前缀


class AuthError(DomainError):
    """凭据问题：未携带 / 已失效 / 已轮换 → 401。"""


class ForbiddenError(DomainError):
    """凭据有效但不持有该边坡当前权限快照 → 403。"""


class ConflictError(DomainError):
    """并发版本冲突或存在未决会签 → 409。"""


class HandoverService:
    # ---------- 班组与会话 ----------
    def list_teams(self) -> list[dict[str, Any]]:
        return [dict(t) for t in store.rows(T_TEAMS) if t["code"] != POOL_CODE]

    def login(self, team_code: str, inspector: str | None = None) -> dict[str, Any]:
        """班组登录（会话签发）。同一班组只保留一个活跃会话，旧会话自动失效。"""
        team = self._team(team_code)
        with store.transaction():
            for sess in store.rows(T_SESSIONS):
                if sess["team_code"] == team_code and sess["active"]:
                    sess["active"] = False
                    sess["revoked_reason"] = "同班组重新登录，旧会话被替换"
            session = {
                "id": store.next_id(T_SESSIONS),
                "token": f"{team_code}-{secrets.token_hex(8)}",
                "team_code": team_code,
                "team_name": team["name"],
                "inspector": (inspector or team["name"]).strip() or team["name"],
                "issued_at": now_text(),
                "active": True,
                "revoked_reason": None,
            }
            store.rows(T_SESSIONS).append(session)
            return dict(session)

    def logout(self, token: str) -> None:
        with store.transaction():
            sess = self._find_session(token)
            if sess:
                sess["active"] = False
                sess["revoked_reason"] = "主动登出"

    def authenticate(self, token: str | None) -> dict[str, Any]:
        """校验会话凭据；失效/被轮换的旧凭据一律拒绝（含断网重试）。"""
        if not token:
            raise AuthError("未携带班组会话凭据，请先登录领取会话")
        sess = self._find_session(token)
        if sess is None:
            raise AuthError("会话不存在或已随移交失效，请用当前班组重新登录")
        if not sess.get("active"):
            raise AuthError(
                f"该会话已失效（{sess.get('revoked_reason') or '凭据已轮换'}），请重新登录"
            )
        return sess

    def _find_session(self, token: str) -> dict[str, Any] | None:
        for sess in store.rows(T_SESSIONS):
            if sess["token"] == token:
                return sess
        return None

    def _team(self, code: str) -> dict[str, Any]:
        for team in store.rows(T_TEAMS):
            if team["code"] == code:
                return team
        raise DomainError(f"班组 {code} 不存在")

    def _rotate_sessions(self, codes: list[str]) -> dict[str, str]:
        """移交事务内联动轮换双方会话：旧凭据全部失活，签发新凭据。

        返回 {班组编号: 新 token}，由接口交还给对应班组；旧 token 无法再用。
        """
        result: dict[str, str] = {}
        for code in dict.fromkeys(c for c in codes if c and c != POOL_CODE):
            for sess in store.rows(T_SESSIONS):
                if sess["team_code"] == code and sess["active"]:
                    sess["active"] = False
                    sess["revoked_reason"] = "边坡移交生效，旧班会话凭据联动失效"
            fresh = {
                "id": store.next_id(T_SESSIONS),
                "token": f"{code}-{secrets.token_hex(8)}",
                "team_code": code,
                "team_name": team_name(code),
                "inspector": team_name(code),
                "issued_at": now_text(),
                "active": True,
                "revoked_reason": None,
            }
            store.rows(T_SESSIONS).append(fresh)
            result[code] = fresh["token"]
        return result

    # ---------- 权限快照查询 ----------
    def active_snapshot(self, slope_id: int) -> dict[str, Any] | None:
        for snap in store.rows(T_SNAPSHOTS):
            if int(snap["slope_id"]) == slope_id and snap.get("active"):
                return snap
        return None

    def _require_owner(self, session: dict[str, Any], slope_id: int) -> dict[str, Any]:
        """越权提交的统一闸口：会话班组必须等于边坡当前生效快照班组。"""
        snap = self.active_snapshot(slope_id)
        if snap is None:
            raise ForbiddenError("该边坡在待认领池，尚无班组持有权限快照，不能提交检查")
        if snap["team_code"] != session["team_code"]:
            raise ForbiddenError(
                f"边坡已不属于{session['team_name']}（当前责任班组：{snap['team_name']}），"
                "上一班的旧会话无权再提交"
            )
        return snap

    def _serialize_slope(self, row: dict[str, Any]) -> dict[str, Any]:
        snap = self.active_snapshot(int(row["id"]))
        data = dict(row)
        if snap is None:
            data["责任班组"] = CLAIM_POOL_TEAM
            data["归属状态"] = "待认领"
            data["当前快照版本"] = None
        else:
            data["责任班组"] = snap["team_name"]
            data["归属状态"] = "已认领"
            data["当前快照版本"] = snap["version"]
        open_orders = [
            o for o in store.rows(T_WORK_ORDERS)
            if int(o["slope_id"]) == int(row["id"]) and o["status"] == ORDER_OPEN
        ]
        data["待整改工单数"] = len(open_orders)
        return data

    # ---------- 边坡台账（列表/详情，同一快照口径） ----------
    def list_slopes(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        team_code: str | None = None,
        pending_claim: bool = False,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        items = [self._serialize_slope(r) for r in store.rows("slope")]
        if keyword:
            items = [r for r in items if keyword in str(r.get("边坡编号", ""))]
        if status:
            items = [r for r in items if r.get("status") == status]
        if team_code:
            items = [r for r in items if self.active_snapshot(int(r["id"]))
                     and self.active_snapshot(int(r["id"]))["team_code"] == team_code]
        if pending_claim:
            items = [r for r in items if self.active_snapshot(int(r["id"])) is None]
        total = len(items)
        start = max(page - 1, 0) * size
        return items[start:start + size], total

    def slope_detail(self, slope_id: int) -> dict[str, Any]:
        row = store.find("slope", slope_id)
        if row is None:
            raise DomainError(f"边坡 {slope_id} 不存在或已归档")
        snapshots = sorted(
            (dict(s) for s in store.rows(T_SNAPSHOTS) if int(s["slope_id"]) == slope_id),
            key=lambda s: int(s["version"]), reverse=True,
        )
        inspections = sorted(
            (dict(i) for i in store.rows(T_INSPECTIONS) if int(i["slope_id"]) == slope_id),
            key=lambda i: int(i["id"]), reverse=True,
        )
        orders = sorted(
            (dict(o) for o in store.rows(T_WORK_ORDERS) if int(o["slope_id"]) == slope_id),
            key=lambda o: int(o["id"]), reverse=True,
        )
        transfers = sorted(
            (t for t in store.rows(T_TRANSFERS) if slope_id in t["slope_ids"]),
            key=lambda t: int(t["id"], ), reverse=True,
        )
        return {
            "slope": self._serialize_slope(row),
            "active_snapshot": dict(self.active_snapshot(slope_id)) if self.active_snapshot(slope_id) else None,
            "snapshots": snapshots,
            "inspections": inspections,
            "work_orders": orders,
            "transfers": [self._public_transfer(t) for t in transfers],
        }

    # ---------- 巡检提交（凭当前快照，越权拒绝） ----------
    def submit_inspection(
        self,
        token: str,
        slope_id: int,
        *,
        content: str,
        abnormal: bool,
        inspector: str | None = None,
    ) -> dict[str, Any]:
        content = (content or "").strip()
        if not content:
            raise DomainError("检查内容不能为空")
        with store.transaction():
            session = self.authenticate(token)
            if store.find("slope", slope_id) is None:
                raise DomainError(f"边坡 {slope_id} 不存在或已归档")
            snap = self._require_owner(session, slope_id)  # 越权在此被拒
            next_no = store.next_id(T_INSPECTIONS)
            inspection = {
                "id": next_no,
                "inspection_no": f"SLIN-{next_no:04d}",
                "slope_id": slope_id,
                "边坡编号": store.find("slope", slope_id)["边坡编号"],
                # 历史归属：提交瞬间的班组与快照版本被固化，移交后不改变
                "team_code": session["team_code"],
                "team_name": snap["team_name"],
                "snapshot_version": snap["version"],
                "inspector": (inspector or session.get("inspector") or snap["team_name"]),
                "content": content,
                "abnormal": bool(abnormal),
                "created_at": now_text(),
            }
            store.rows(T_INSPECTIONS).append(inspection)
            store.find("slope", slope_id)["最近巡检"] = inspection["created_at"]
            order = None
            if abnormal:
                order = self._open_work_order(slope_id, inspection)
            return {"inspection": inspection, "work_order": order}

    def _open_work_order(self, slope_id: int, inspection: dict[str, Any]) -> dict[str, Any]:
        slope = store.find("slope", slope_id)
        next_seq = store.next_id(T_WORK_ORDERS)
        order = {
            "id": next_seq,
            "order_no": f"SLWO-{next_seq:04d}",
            "slope_id": slope_id,
            "边坡编号": slope["边坡编号"],
            "title": f"{slope['边坡编号']}边坡整改：{inspection['content'][:30]}",
            "source_inspection_id": inspection["id"],
            # 工单归属跟随提交时刻的权限快照；移交事务里再整体改派
            "owner_code": inspection["team_code"],
            "owner_name": inspection["team_name"],
            "status": ORDER_OPEN,
            "created_at": now_text(),
            "closed_at": None,
        }
        store.rows(T_WORK_ORDERS).append(order)
        slope["abnormal"] = True
        self._sync_project(slope, order)
        # 进当前责任班组的待办——修复“接收队看不到整改工单”
        self._upsert_todo(
            inspection["team_code"],
            "整改工单",
            order["id"],
            f"整改工单 {order['order_no']}：{order['title']}",
        )
        return order

    def close_work_order(self, token: str, order_id: int) -> dict[str, Any]:
        with store.transaction():
            session = self.authenticate(token)
            order = next((o for o in store.rows(T_WORK_ORDERS) if int(o["id"]) == order_id), None)
            if order is None:
                raise DomainError(f"工单 {order_id} 不存在")
            if order["owner_code"] != session["team_code"]:
                raise ForbiddenError("工单当前不属于本班组，不能办结")
            if order["status"] != ORDER_OPEN:
                raise DomainError("工单已办结，无需重复操作")
            order["status"] = ORDER_DONE
            order["closed_at"] = now_text()
            self._close_todos("整改工单", order["id"])
            slope = store.find("slope", int(order["slope_id"]))
            if slope is not None:
                slope["abnormal"] = any(
                    o["slope_id"] == slope["id"] and o["status"] == ORDER_OPEN
                    for o in store.rows(T_WORK_ORDERS)
                )
            self._sync_project(slope, order)
            return dict(order)

    def list_work_orders(self, team_code: str | None = None) -> list[dict[str, Any]]:
        rows = store.rows(T_WORK_ORDERS)
        if team_code:
            rows = [o for o in rows if o["owner_code"] == team_code]
        return [dict(o) for o in sorted(rows, key=lambda o: int(o["id"]), reverse=True)]

    def list_inspections(self, team_code: str | None = None) -> list[dict[str, Any]]:
        rows = store.rows(T_INSPECTIONS)
        if team_code:
            rows = [i for i in rows if i["team_code"] == team_code]
        return [dict(i) for i in sorted(rows, key=lambda i: int(i["id"]), reverse=True)]

    # ---------- 班组待办 ----------
    def list_todos(self, team_code: str) -> list[dict[str, Any]]:
        return [
            dict(t) for t in sorted(
                (t for t in store.rows(T_TODOS) if t["team_code"] == team_code),
                key=lambda t: int(t["id"]), reverse=True,
            )
        ]

    def _upsert_todo(self, team_code: str, kind: str, ref_id: int, title: str) -> None:
        for todo in store.rows(T_TODOS):
            if (todo["team_code"] == team_code and todo["type"] == kind
                    and int(todo["ref_id"]) == ref_id and todo["status"] == "待办"):
                todo["title"] = title
                return
        store.rows(T_TODOS).append({
            "id": store.next_id(T_TODOS),
            "team_code": team_code,
            "type": kind,
            "ref_id": ref_id,
            "title": title,
            "status": "待办",
            "created_at": now_text(),
            "closed_at": None,
        })

    def _close_todos(self, kind: str, ref_id: int, *, team_code: str | None = None) -> None:
        for todo in store.rows(T_TODOS):
            if (todo["type"] == kind and int(todo["ref_id"]) == ref_id
                    and todo["status"] == "待办"
                    and (team_code is None or todo["team_code"] == team_code)):
                todo["status"] = "已办结"
                todo["closed_at"] = now_text()

    # ---------- 工程清单回写 ----------
    def _sync_project(self, slope: dict[str, Any], order: dict[str, Any] | None) -> None:
        """把移交/工单结论回写到养护工程清单（按关联边坡幂等 upsert）。"""
        projects = store.rows("project")
        code = f"{PROJECT_PREFIX}{slope['边坡编号']}"
        row = next((p for p in projects if p.get("工程编号") == code), None)
        open_orders = [
            o for o in store.rows(T_WORK_ORDERS)
            if int(o["slope_id"]) == int(slope["id"]) and o["status"] == ORDER_OPEN
        ]
        owner = self.active_snapshot(int(slope["id"]))
        owner_name = owner["team_name"] if owner else CLAIM_POOL_TEAM
        if open_orders:
            status, pending, abnormal = "施工中", True, True
        else:
            status, pending, abnormal = "已竣工", False, False
        values = {
            "工程编号": code,
            "工程名称": f"{slope['边坡编号']}边坡整改工程",
            "工程类型": "边坡整改",
            "施工路段": slope.get("所属路段", ""),
            "承建单位": owner_name,
            "工程状态": status,
            "关联边坡": slope["边坡编号"],
            "责任班组": owner_name,
        }
        if row is None:
            row = {"id": store.next_id("project"), "开工日期": now_text()[:10],
                   "竣工日期": None}
            row.update(values)
            row["status"] = status
            row["pending"] = pending
            row["abnormal"] = abnormal
            projects.append(row)
        else:
            row.update(values)
            row["status"] = status
            row["pending"] = pending
            row["abnormal"] = abnormal
            if not open_orders and not row.get("竣工日期"):
                row["竣工日期"] = now_text()[:10]

    # ---------- 移交会签 ----------
    def _public_transfer(self, transfer: dict[str, Any]) -> dict[str, Any]:
        """脱敏移交记录：新会话凭据只在签署当次响应里返回，不进任何列表/详情查询。"""
        data = dict(transfer)
        data.pop("rotated_tokens", None)
        return data

    def list_transfers(self, status: str | None = None) -> list[dict[str, Any]]:
        rows = store.rows(T_TRANSFERS)
        if status:
            rows = [t for t in rows if t["status"] == status]
        return [self._public_transfer(t) for t in sorted(rows, key=lambda t: int(t["id"]), reverse=True)]

    def create_transfer(
        self,
        token: str,
        slope_ids: list[int],
        to_code: str | None,
        *,
        remark: str | None = None,
    ) -> dict[str, Any]:
        """发起移交：仅当前责任班组可发起；接收方缺省为待认领池。"""
        ids = sorted({int(i) for i in slope_ids})
        if not ids:
            raise DomainError("请至少选择一处边坡参与移交")
        with store.transaction():
            session = self.authenticate(token)
            from_code = session["team_code"]
            if to_code is not None:
                if to_code == from_code:
                    raise DomainError("接收班组不能与移交班组相同")
                self._team(to_code)
            slopes = [store.find("slope", sid) for sid in ids]
            if any(s is None for s in slopes):
                raise DomainError("存在已归档或不存在的边坡，移交已拦下")

            base_versions: dict[int, int] = {}
            for sid in ids:
                snap = self.active_snapshot(sid)
                if snap is None:
                    raise ForbiddenError(
                        f"边坡 {store.find('slope', sid)['边坡编号']} 在待认领池，"
                        "请走认领流程而非班组移交"
                    )
                if snap["team_code"] != from_code:
                    raise ForbiddenError(
                        f"边坡 {store.find('slope', sid)['边坡编号']}不属于{session['team_name']}，不能发起移交"
                    )
                # 并发控制：同一边坡存在未决会签时拒绝重复发起
                blocked = next(
                    (t for t in store.rows(T_TRANSFERS)
                     if t["status"] == DRAFT and sid in t["slope_ids"]), None,
                )
                if blocked is not None:
                    raise ConflictError(
                        f"边坡 {store.find('slope', sid)['边坡编号']}已有进行中的移交单 "
                        f"{blocked['transfer_no']}，并发移交只接受一个签署版本"
                    )
                base_versions[sid] = int(snap["version"])

            next_tid = store.next_id(T_TRANSFERS)
            transfer = {
                "id": next_tid,
                "transfer_no": f"SLTR-{next_tid:04d}",
                "slope_ids": ids,
                "from_code": from_code,
                "from_name": session["team_name"],
                "to_code": to_code,
                "to_name": team_name(to_code) if to_code else CLAIM_POOL_TEAM,
                "status": DRAFT,
                "initiated_by": session["team_name"],
                "initiated_at": now_text(),
                "base_versions": base_versions,
                "signatures": [],
                "effective_at": None,
                "remark": (remark or "").strip(),
            }
            store.rows(T_TRANSFERS).append(transfer)
            self._upsert_todo(
                from_code, "移交会签", transfer["id"],
                f"移交单 {transfer['transfer_no']} 待{'对方会签' if to_code else '本方签署移交待认领池'}",
            )
            if to_code:
                self._upsert_todo(
                    to_code, "移交会签", transfer["id"],
                    f"移交单 {transfer['transfer_no']} 待接收会签",
                )
            return dict(transfer)

    def sign_transfer(
        self, token: str, transfer_id: int, *, agree: bool, signer: str | None = None,
    ) -> dict[str, Any]:
        """一方签署；双方签署齐备的当刻事务性生效。拒签则移交单作废。"""
        with store.transaction():
            session = self.authenticate(token)
            transfer = next(
                (t for t in store.rows(T_TRANSFERS) if int(t["id"]) == transfer_id), None,
            )
            if transfer is None:
                raise DomainError(f"移交单 {transfer_id} 不存在")
            if transfer["status"] != DRAFT:
                raise ConflictError(f"移交单 {transfer['transfer_no']} 已{transfer['status']}，不能重复签署")

            code = session["team_code"]
            parties = {transfer["from_code"]}
            if transfer["to_code"]:
                parties.add(transfer["to_code"])
            if code not in parties:
                raise ForbiddenError("只有移交双方可以签署该移交单")
            if any(sig["team_code"] == code for sig in transfer["signatures"]):
                raise ConflictError("本班组已签署，不能重复签署")

            sig = {
                "team_code": code,
                "team_name": session["team_name"],
                "signer": (signer or session.get("inspector") or session["team_name"]),
                "agreed": bool(agree),
                "signed_at": now_text(),
            }
            transfer["signatures"].append(sig)
            self._close_todos("移交会签", int(transfer["id"]), team_code=code)

            if not agree:
                transfer["status"] = REJECTED
                for other in parties - {code}:
                    self._close_todos("移交会签", int(transfer["id"]), team_code=other)
                return dict(transfer)

            required = parties  # 入池移交只需移交方签署
            signed_codes = {s["team_code"] for s in transfer["signatures"] if s["agreed"]}
            conflict_message: str | None = None
            if required <= signed_codes:
                conflict_message = self._execute_transfer(transfer)
            result = dict(transfer)
            # 作废结论已随本次事务落库；冲突在事务外抛出，避免回滚掉“作废”标记
            if conflict_message is not None:
                result["_conflict"] = conflict_message
            return result

    def _execute_transfer(self, transfer: dict[str, Any]) -> str | None:
        """双方签署齐备后的事务性生效。

        返回 None 表示成功生效；返回字符串表示检测到并发版本竞争，此时本移交单
        已在事务内标记为“已作废”，调用方应在事务提交后再把冲突作为 409 抛出。
        """
        # 生效前复检基础版本：并发下被别的移交抢先则本版本作废，只接受一个签署版本
        for sid, base_ver in transfer["base_versions"].items():
            snap = self.active_snapshot(sid)
            if snap is None or int(snap["version"]) != int(base_ver):
                transfer["status"] = VOID
                self._close_todos("移交会签", int(transfer["id"]))
                return (
                    f"边坡 {store.find('slope', sid)['边坡编号']}已被另一签署版本抢先移交，"
                    f"移交单 {transfer['transfer_no']} 作废"
                )

        to_code = transfer["to_code"]  # None 表示迁入待认领池
        effective_at = max(s["signed_at"] for s in transfer["signatures"])
        transfer["status"] = SIGNED
        transfer["effective_at"] = effective_at  # 生效时间以双方签署记录为准

        target_code = to_code or POOL_CODE
        reason = f"移交单 {transfer['transfer_no']} 生效"
        for sid in transfer["slope_ids"]:
            issue_snapshot(
                sid, to_code, reason=reason, actor="双方会签",
                transfer_id=int(transfer["id"]), issued_at=effective_at,
            )
            slope = store.find("slope", sid)
            # 历史巡检归属保留：巡检行不动；只改派在办整改工单
            for order in store.rows(T_WORK_ORDERS):
                if int(order["slope_id"]) == sid and order["status"] == ORDER_OPEN:
                    order["owner_code"] = target_code
                    order["owner_name"] = team_name(target_code) or CLAIM_POOL_TEAM
                    self._upsert_todo(
                        target_code, "整改工单", int(order["id"]),
                        f"接收入库·整改工单 {order['order_no']}：{order['title']}",
                    )
                    self._close_todos("整改工单", int(order["id"]), team_code=transfer["from_code"])
            # 工程清单回写新责任班组与结论
            any_order = next(
                (o for o in store.rows(T_WORK_ORDERS) if int(o["slope_id"]) == sid), None,
            )
            self._sync_project(slope, any_order)
            # 双方待办：接收方“已接收”入账；移交方相关会签/工单待办已在前面办结
            self._upsert_todo(
                target_code, "边坡接收", sid,
                f"边坡 {slope['边坡编号']} 已接收，纳入管养（{transfer['transfer_no']}）",
            )

        # 会话凭据联动轮换：旧班会话立即失效，新会话随事务一起签发
        rotated = self._rotate_sessions([transfer["from_code"], to_code])
        transfer["rotated_tokens"] = rotated
        return None

    # ---------- 待认领池 ----------
    def claim_pool(self) -> list[dict[str, Any]]:
        rows = [
            self._serialize_slope(r) for r in store.rows("slope")
            if self.active_snapshot(int(r["id"])) is None
        ]
        return rows

    def claim(self, token: str, slope_id: int) -> dict[str, Any]:
        """无归属边坡只能先在待认领池，由班组显式认领后才获得权限快照。"""
        with store.transaction():
            session = self.authenticate(token)
            slope = store.find("slope", slope_id)
            if slope is None:
                raise DomainError(f"边坡 {slope_id} 不存在或已归档")
            if self.active_snapshot(slope_id) is not None:
                raise ConflictError("该边坡已有归属，不在待认领池")
            issue_snapshot(
                slope_id, session["team_code"], reason="待认领池认领",
                actor=session["team_name"],
            )
            for order in store.rows(T_WORK_ORDERS):
                if int(order["slope_id"]) == slope_id and order["status"] == ORDER_OPEN:
                    order["owner_code"] = session["team_code"]
                    order["owner_name"] = session["team_name"]
                    self._upsert_todo(
                        session["team_code"], "整改工单", int(order["id"]),
                        f"认领入库·整改工单 {order['order_no']}：{order['title']}",
                    )
            self._sync_project(slope, None)
            self._upsert_todo(
                session["team_code"], "边坡接收", slope_id,
                f"边坡 {slope['边坡编号']} 已从待认领池认领，纳入管养",
            )
            next_tid = store.next_id(T_TRANSFERS)
            transfer_note = {
                "id": next_tid,
                "transfer_no": f"SLCL-{next_tid:04d}",
                "slope_ids": [slope_id],
                "from_code": POOL_CODE,
                "from_name": CLAIM_POOL_TEAM,
                "to_code": session["team_code"],
                "to_name": session["team_name"],
                "status": SIGNED,
                "initiated_by": session["team_name"],
                "initiated_at": now_text(),
                "base_versions": {},
                "signatures": [{
                    "team_code": session["team_code"], "team_name": session["team_name"],
                    "signer": session["team_name"], "agreed": True, "signed_at": now_text(),
                }],
                "effective_at": now_text(),
                "remark": "待认领池认领",
                "rotated_tokens": self._rotate_sessions([session["team_code"]]),
            }
            store.rows(T_TRANSFERS).append(transfer_note)
            return {"slope": self._serialize_slope(slope), "transfer": transfer_note}


handover_service = HandoverService()
