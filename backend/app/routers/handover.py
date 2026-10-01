"""边坡移交接口：班组会话、巡检提交、整改工单、会签移交、待认领池、班组待办。

所有写操作要求请求头携带 ``Authorization: Bearer <token>``（登录接口签发）。
错误码口径：

- 401：未携带/会话失效/凭据已随移交轮换（旧班会话、断网重试都在此被拒）；
- 403：凭据有效但不持有该边坡当前权限快照（越权提交）；
- 409：并发移交版本冲突（只接受一个签署版本）或状态不允许；
- 400：业务字段不满足。
"""
from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from app.schemas import ActionResult, PageResult
from app.services.handover import (
    AuthError,
    ConflictError,
    DomainError,
    ForbiddenError,
    handover_service,
)

router = APIRouter(prefix="/api/slope-handover", tags=["边坡移交"])


def token_from(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.split(" ", 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return authorization.strip()


def raise_for(error: DomainError) -> None:
    if isinstance(error, AuthError):
        raise HTTPException(status_code=401, detail=str(error))
    if isinstance(error, ForbiddenError):
        raise HTTPException(status_code=403, detail=str(error))
    if isinstance(error, ConflictError):
        raise HTTPException(status_code=409, detail=str(error))
    raise HTTPException(status_code=400, detail=str(error))


# ---------- 请求模型 ----------
class LoginPayload(BaseModel):
    team_code: str
    inspector: str | None = None


class InspectionPayload(BaseModel):
    content: str = ""
    abnormal: bool = False
    inspector: str | None = None


class TransferPayload(BaseModel):
    slope_ids: list[int] = Field(default_factory=list)
    # 不传/为 null 表示迁入待认领池
    to_code: str | None = None
    remark: str | None = None


class SignPayload(BaseModel):
    agree: bool = True
    signer: str | None = None


class IdPayload(BaseModel):
    id: int


# ---------- 班组与会话 ----------
@router.get("/teams")
def teams() -> dict:
    return {"items": handover_service.list_teams()}


@router.post("/login", response_model=ActionResult)
def login(payload: LoginPayload) -> ActionResult:
    try:
        session = handover_service.login(payload.team_code, payload.inspector)
    except DomainError as error:
        raise_for(error)
    return ActionResult(ok=True, message=f"{session['team_name']} 会话已签发", entry=session)


@router.post("/logout", response_model=ActionResult)
def logout(authorization: str | None = Header(default=None)) -> ActionResult:
    handover_service.logout(token_from(authorization) or "")
    return ActionResult(ok=True, message="会话已退出")


# ---------- 边坡台账（列表/详情同源快照） ----------
@router.get("/slopes", response_model=PageResult[dict])
def slopes(
    keyword: str | None = None,
    status: str | None = None,
    team_code: str | None = None,
    pending_claim: bool = False,
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = handover_service.list_slopes(
        keyword=keyword, status=status, team_code=team_code,
        pending_claim=pending_claim, page=page, size=size,
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/slopes/{slope_id}")
def slope_detail(slope_id: int) -> dict:
    try:
        return handover_service.slope_detail(slope_id)
    except DomainError as error:
        raise_for(error)


# ---------- 巡检提交 ----------
@router.post("/slopes/{slope_id}/inspections", response_model=ActionResult)
def submit_inspection(
    slope_id: int,
    payload: InspectionPayload,
    authorization: str | None = Header(default=None),
) -> ActionResult:
    try:
        result = handover_service.submit_inspection(
            token_from(authorization), slope_id,
            content=payload.content, abnormal=payload.abnormal,
            inspector=payload.inspector,
        )
    except DomainError as error:
        raise_for(error)
    message = "检查已提交"
    if result["work_order"] is not None:
        message += f"，整改工单 {result['work_order']['order_no']} 已进入本班组待办"
    return ActionResult(ok=True, message=message, entry=result)


@router.get("/inspections")
def inspections(authorization: str | None = Header(default=None)) -> dict:
    try:
        session = handover_service.authenticate(token_from(authorization))
    except DomainError as error:
        raise_for(error)
    return {"items": handover_service.list_inspections(session["team_code"])}


# ---------- 整改工单 ----------
@router.get("/work-orders")
def work_orders(authorization: str | None = Header(default=None)) -> dict:
    try:
        session = handover_service.authenticate(token_from(authorization))
    except DomainError as error:
        raise_for(error)
    return {"items": handover_service.list_work_orders(session["team_code"])}


@router.post("/work-orders/{order_id}/close", response_model=ActionResult)
def close_work_order(
    order_id: int,
    authorization: str | None = Header(default=None),
) -> ActionResult:
    try:
        order = handover_service.close_work_order(token_from(authorization), order_id)
    except DomainError as error:
        raise_for(error)
    return ActionResult(ok=True, message=f"工单 {order['order_no']} 已办结", entry=order)


# ---------- 班组待办 ----------
@router.get("/todos")
def todos(authorization: str | None = Header(default=None)) -> dict:
    try:
        session = handover_service.authenticate(token_from(authorization))
    except DomainError as error:
        raise_for(error)
    return {"items": handover_service.list_todos(session["team_code"])}


# ---------- 移交会签 ----------
@router.get("/transfers")
def transfers(status: str | None = None) -> dict:
    return {"items": handover_service.list_transfers(status)}


@router.post("/transfers", response_model=ActionResult)
def create_transfer(
    payload: TransferPayload,
    authorization: str | None = Header(default=None),
) -> ActionResult:
    try:
        transfer = handover_service.create_transfer(
            token_from(authorization), payload.slope_ids, payload.to_code, remark=payload.remark,
        )
    except DomainError as error:
        raise_for(error)
    target = transfer["to_name"]
    return ActionResult(
        ok=True,
        message=f"移交单 {transfer['transfer_no']} 已发起，待{target}双方会签",
        entry=transfer,
    )


@router.post("/transfers/{transfer_id}/sign", response_model=ActionResult)
def sign_transfer(
    transfer_id: int,
    payload: SignPayload,
    authorization: str | None = Header(default=None),
) -> ActionResult:
    try:
        transfer = handover_service.sign_transfer(
            token_from(authorization), transfer_id, agree=payload.agree, signer=payload.signer,
        )
    except DomainError as error:
        raise_for(error)
    # 并发竞争失败：作废已随事务提交，这里再以 409 告知调用方
    conflict = transfer.pop("_conflict", None)
    if conflict:
        raise HTTPException(status_code=409, detail=conflict)
    if transfer["status"] == "已生效":
        return ActionResult(
            ok=True,
            message=(
                f"移交单 {transfer['transfer_no']} 已于 {transfer['effective_at']} 生效："
                "台账、工程清单、工单与待办已回写，双方旧会话已失效"
            ),
            entry=transfer,
        )
    if transfer["status"] == "已驳回":
        return ActionResult(ok=False, message=f"移交单 {transfer['transfer_no']} 已被拒签驳回",
                            entry=transfer)
    return ActionResult(ok=True, message=f"移交单 {transfer['transfer_no']} 已记录签署，待对方会签",
                        entry=transfer)


# ---------- 待认领池 ----------
@router.get("/claim-pool")
def claim_pool() -> dict:
    return {"items": handover_service.claim_pool()}


@router.post("/claims", response_model=ActionResult)
def claim(payload: IdPayload, authorization: str | None = Header(default=None)) -> ActionResult:
    try:
        result = handover_service.claim(token_from(authorization), payload.id)
    except DomainError as error:
        raise_for(error)
    return ActionResult(ok=True, message="边坡已从待认领池认领，权限快照已签发", entry=result)
