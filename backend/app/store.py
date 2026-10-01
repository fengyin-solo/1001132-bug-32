"""内存数据仓库：给每个业务模块准备一份可筛选、可流转的示例数据。

真实项目里这里会换成数据库访问层；当前实现只依赖标准库，保证克隆下来就能起。

移交域对一致性要求高：会话签发、归属变更、工单回写必须在同一个事务里要么全部
生效、要么全部回滚，因此这里提供：

- ``transaction()``：进程内互斥锁 + 快照回滚，模拟数据库事务；
- ``ensure_table()``：运行期建表（移交、巡检、工单、待办、班组、会话）。
"""
from __future__ import annotations

import contextlib
import threading
from typing import Any, Iterator

from app.seed import SEED_ROWS


class Store:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._tables: dict[str, list[dict[str, Any]]] = {
            name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()
        }

    @contextlib.contextmanager
    def transaction(self) -> Iterator[None]:
        """以表级快照模拟事务：块内任意业务异常都会整体回滚。

        边坡移交的提交动作（台账归属、工程清单、班组待办、双方凭据轮换）必须在
        同一个事务里完成，避免“旧班会话仍可提交、接收队却看不到工单”这类半提交。
        """
        self._lock.acquire()
        snapshot = {name: [dict(row) for row in rows] for name, rows in self._tables.items()}
        try:
            yield
        except Exception:
            # 整体还原到事务前状态，调用方拿到异常后按业务错误返回。
            self._tables = snapshot
            raise
        finally:
            self._lock.release()

    def ensure_table(self, name: str) -> list[dict[str, Any]]:
        return self._tables.setdefault(name, [])

    def module_names(self) -> list[str]:
        # 概览只统计业务模块，移交域的内部表（会话/快照/工单等）不计入
        return sorted(name for name in self._tables if name in SEED_ROWS)

    def rows(self, module: str) -> list[dict[str, Any]]:
        return self._tables.setdefault(module, [])

    def find(self, module: str, entry_id: int) -> dict[str, Any] | None:
        for row in self.rows(module):
            if int(row.get("id", 0)) == entry_id:
                return row
        return None

    def next_id(self, module: str) -> int:
        return max((int(row.get("id", 0)) for row in self.rows(module)), default=0) + 1

    def overview(self) -> dict[str, object]:
        modules: list[dict[str, object]] = []
        for name in self.module_names():
            rows = self.rows(name)
            modules.append({
                "name": name,
                "created": len(rows),
                "pending": sum(1 for row in rows if row.get("pending")),
                "abnormal": sum(1 for row in rows if row.get("abnormal")),
            })
        cards = [
            {"label": "业务模块", "value": len(modules)},
            {"label": "今日新增", "value": sum(int(item["created"]) for item in modules)},
            {"label": "待处理", "value": sum(int(item["pending"]) for item in modules)},
            {"label": "异常量", "value": sum(int(item["abnormal"]) for item in modules)},
        ]
        return {"cards": cards, "modules": modules}


store = Store()
