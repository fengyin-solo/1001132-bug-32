"""移交生效时钟：生效时间一律以「双方签署记录」为准。

设计要点
--------
- 我们不直接信任客户端上送的系统时间，避免断网重放把旧授权「往前拨」复活。
- 每次签署由服务端单调时钟给出一个严格递增的逻辑序号（seq），并附上可读时间戳。
- 移交的 ``effective_at`` 取交出、接收双方两条签署记录中**较晚**的一条，
  即「双方都签署完成」的那一刻；只有单方签署不生效。
- 时钟只增不减、不可回拨，从根上杜绝「断网后再次提交让旧凭据重新生效」。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone


def _now_iso() -> str:
    # 统一带时区的 ISO8601，便于前后端比对。
    return datetime.now(timezone.utc).isoformat()


@dataclass
class SignClock:
    """单调签署时钟：线程安全，序号严格递增。"""

    _seq: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def tick(self) -> tuple[int, str]:
        """落一条签署记录，返回 (单调序号, ISO8601 时间戳)。"""
        with self._lock:
            self._seq += 1
            return self._seq, _now_iso()

    @property
    def seq(self) -> int:
        with self._lock:
            return self._seq


# 进程内单例：所有签署、生效时间都经它发放。
sign_clock = SignClock()
