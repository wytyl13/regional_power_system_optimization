#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/13 17:05
@Author  : weiyutao
@File    : errors.py
统一异常体系（服务内部规范；外部 HTTP 响应合同不变——仍是 FastAPI 的 {"detail": ...}）。

规则：
  - 业务代码只 raise 本文件定义的异常（或让它冒出来的 ValueError，见下）
  - 路由端点不写 try/except 样板，异常由 app 级 handler 统一转 HTTP
  - ValueError = 模板/曲线数据校验错 → 422（沿用现有语义，算法侧 reader/parse 也抛它）
"""
from __future__ import annotations

from fastapi import HTTPException, status


class BizError(Exception):
    """业务错误基类：code 即 HTTP 状态码，message 给前端看人话。"""

    def __init__(self, message: str, code: int = status.HTTP_422_UNPROCESSABLE_ENTITY):
        """记录人话消息与 HTTP 状态码。

        Args:
            message (str): 给前端看的错误消息。
            code (int): HTTP 状态码（默认 422）。

        Returns:
            None
        """
        super().__init__(message)
        self.message = message
        self.code = code


class NotFoundError(BizError):
    def __init__(self, message: str):
        """固定 404（资源不存在）。

        Args:
            message (str): 给前端看的错误消息。

        Returns:
            None
        """
        super().__init__(message, code=status.HTTP_404_NOT_FOUND)


class ConflictError(BizError):
    def __init__(self, message: str):
        """固定 409（状态冲突，如未解完取结果）。

        Args:
            message (str): 给前端看的错误消息。

        Returns:
            None
        """
        super().__init__(message, code=status.HTTP_409_CONFLICT)


class ValidationError(BizError):
    """模板/配置校验失败（与 ValueError 同语义，422）。"""

    def __init__(self, message: str):
        """固定 422（模板/配置校验失败）。

        Args:
            message (str): 给前端看的错误消息。

        Returns:
            None
        """
        super().__init__(message, code=status.HTTP_422_UNPROCESSABLE_ENTITY)


def install_handlers(app) -> None:
    """把 BizError/ValueError 统一转 HTTPException（响应形状 = FastAPI 原生）。

    Args:
        app (FastAPI): 应用实例。

    Returns:
        None
    """
    @app.exception_handler(BizError)
    async def _biz(request, exc: BizError):
        raise HTTPException(exc.code, exc.message)

    @app.exception_handler(ValueError)
    async def _value(request, exc: ValueError):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
