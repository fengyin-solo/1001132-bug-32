"""移交子系统的领域实体与状态常量。

全部用普通 dataclass + 基本类型，内存存储即可序列化；切到真实数据库时，
这些类可以平滑换成 ORM 模型，业务层（service）不需要改动。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

# ---- 边坡在移交生命周期里的归属状态（边坡台账、列表、详情共用同一份） ----
SlopeOwnerState = Literal["归属班组", "待认领"]

# ---- 移交单状态机 ----
# 草稿 -> 待接收签署 -> 已生效；任何环节失败/冲突都会留下结论，绝不半生效。
HANDOVER_DRAFT = "草稿"        # 交出方已发起，尚未完成双方签署
HANDOVER_PENDING = "待接收签署"  # 交出方已签，等待接收方签署
HANDOVER_EFFECTIVE = "已生效"   # 双方签署完成，结论已回写
HANDOVER_VOID = "已作废"        # 被更新版本取代或显式作废（保留留痕，不再生效）

# 签署角色
ROLE_GIVER = "交出方"
ROLE_TAKER = "接收方"

# 巡检 / 整改工单状态
INSP_OPEN = "待整改"
INSP_DONE = "已整改"

# 会话状态
SESSION_ACTIVE = "有效"
SESSION_REVOKED = "已失效"

# 待认领池里的认领结论来源
POOL_PENDING = "待认领"


@dataclass
class Team:
    """养护班组。"""

    code: str                       # 班组编号，主键
    name: str                       # 班组名称

    def public(self) -> dict[str, Any]:
        return {"code": self.code, "name": self.name}


@dataclass
class Session:
    """班组会话凭据（上一班签发，移交后必须换发）。

    - ``token``      凭据标识（不透明字符串），调用写接口时必须出示。
    - ``epoch``      签发纪元：每次成功换发 +1；旧凭据的 epoch 立刻落后。
    - ``snapshot``   签发时刻固化的「权限快照」（可访问的边坡/工程范围）。
    - ``valid_from`` 签发时间（seq, iso），仅在该 seq 之后才有效。
    """

    token: str
    team_code: str
    epoch: int
    issued_seq: int
    issued_at: str
    state: str = SESSION_ACTIVE
    # 权限快照：边坡 id 集合与工程 id 集合（签发那一刻冻结，事后归属变了也不改）。
    slope_ids: list[int] = field(default_factory=list)
    project_ids: list[int] = field(default_factory=list)
    # 失效留痕
    revoked_seq: int | None = None
    revoked_at: str | None = None
    revoke_reason: str | None = None

    def public(self) -> dict[str, Any]:
        return {
            "token": self.token,
            "teamCode": self.team_code,
            "epoch": self.epoch,
            "state": self.state,
            "issuedSeq": self.issued_seq,
            "issuedAt": self.issued_at,
            "scope": {"slopeIds": list(self.slope_ids), "projectIds": list(self.project_ids)},
            "revokedAt": self.revoked_at,
            "revokeReason": self.revoke_reason,
        }


@dataclass
class SignatureRecord:
    """单方签署记录：移交生效时间就是由这两条记录决定的。"""

    role: str            # 交出方 / 接收方
    team_code: str
    signer: str
    seq: int             # 单调时钟序号
    signed_at: str       # ISO8601
    # 客户端本次提交携带的幂等键，断网重连用：同一键只认第一次。
    nonce: str | None = None


@dataclass
class HandoverOrder:
    """边坡移交单。

    一张移交单可覆盖多个边坡（一次班组交接通常是一批边坡）。
    - ``giver_code``  当前交出班组；None 表示来源是「待认领池/无归属」。
    - ``taker_code``  接收班组；None 表示移交给「待认领池」（缺归属迁移）。
    - ``version``     乐观并发版本号，签署时做 CAS，仅一个版本能生效。
    - ``slope_ids``   本次移交覆盖的边坡。
    """

    id: int
    slope_ids: list[int]
    giver_code: str | None
    taker_code: str | None
    note: str
    status: str = HANDOVER_DRAFT
    version: int = 1
    signatures: list[SignatureRecord] = field(default_factory=list)
    # 生效结论
    effective_seq: int | None = None
    effective_at: str | None = None
    # 本次生效时换发出的新凭据 / 失效的旧凭据（事务联动留痕）
    issued_tokens: list[str] = field(default_factory=list)
    revoked_tokens: list[str] = field(default_factory=list)
    # 回写影响面（台账/工程清单/待办），事务成功后回填，便于审计。
    affected: dict[str, Any] = field(default_factory=dict)

    @property
    def giver_signed(self) -> bool:
        return any(s.role == ROLE_GIVER for s in self.signatures)

    @property
    def taker_signed(self) -> bool:
        return any(s.role == ROLE_TAKER for s in self.signatures)

    def signature_of(self, role: str) -> SignatureRecord | None:
        for sig in self.signatures:
            if sig.role == role:
                return sig
        return None

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "slopeIds": list(self.slope_ids),
            "giverCode": self.giver_code,
            "takerCode": self.taker_code,
            "note": self.note,
            "status": self.status,
            "version": self.version,
            "signatures": [
                {
                    "role": s.role,
                    "teamCode": s.team_code,
                    "signer": s.signer,
                    "seq": s.seq,
                    "signedAt": s.signed_at,
                    "nonce": s.nonce,
                }
                for s in self.signatures
            ],
            "effectiveSeq": self.effective_seq,
            "effectiveAt": self.effective_at,
            "issuedTokens": list(self.issued_tokens),
            "revokedTokens": list(self.revoked_tokens),
            "affected": self.affected,
        }


@dataclass
class Inspection:
    """巡检/检查记录。

    关键字段
    --------
    - ``submitted_by_team`` 提交时命中的班组（按当前权限快照校验通过）。
    - ``slope_owner_team`` 提交时边坡台账上的归属班组（归属快照）。
    - ``historical_team``   历史巡检归属：一旦写下永不更改，移交不重写历史。
    - ``session_token/epoch`` 是哪一版凭据提交的，便于追溯。
    """

    id: int
    slope_id: int
    code: str                       # 巡检单号
    content: str                    # 检查内容
    root_cause: str                 # 同一边坡防护根因（列表/详情共用同一行）
    need_rectify: bool              # 是否需要整改（产生整改工单）
    status: str
    submitted_by_team: str
    slope_owner_team: str
    historical_team: str
    session_token: str
    session_epoch: int
    submitted_seq: int
    submitted_at: str

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "slopeId": self.slope_id,
            "code": self.code,
            "content": self.content,
            "rootCause": self.root_cause,
            "needRectify": self.need_rectify,
            "status": self.status,
            "submittedByTeam": self.submitted_by_team,
            "slopeOwnerTeam": self.slope_owner_team,
            "historicalTeam": self.historical_team,
            "sessionEpoch": self.session_epoch,
            "submittedSeq": self.submitted_seq,
            "submittedAt": self.submitted_at,
        }


@dataclass
class WorkOrder:
    """整改工单（班组待办）。

    移交生效时，未关闭工单的归属随权限快照一并切给接收班组，
    这样「接收队看得到整改工单」。历史归属保留在 ``historical_team``。
    """

    id: int
    inspection_id: int
    slope_id: int
    code: str                       # 工单号
    title: str
    root_cause: str
    owner_team: str                 # 当前归属班组（接收队据此看到待办）
    historical_team: str            # 首次归属（历史归属不丢）
    status: str = INSP_OPEN
    created_seq: int = 0
    created_at: str = ""
    closed_seq: int | None = None
    closed_at: str | None = None
    # 工单随移交迁移的轨迹
    transfers: list[dict[str, Any]] = field(default_factory=list)

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "inspectionId": self.inspection_id,
            "slopeId": self.slope_id,
            "code": self.code,
            "title": self.title,
            "rootCause": self.root_cause,
            "ownerTeam": self.owner_team,
            "historicalTeam": self.historical_team,
            "status": self.status,
            "createdAt": self.created_at,
            "closedAt": self.closed_at,
            "transfers": self.transfers,
        }
