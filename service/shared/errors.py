#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/13 17:05
@Author  : weiyutao
@File    : errors.py
统一异常体系 + 统一响应壳（2026-10-20 起 HTTP 合同改为 service-api skill §6/§7：
所有响应（含错误）一律 {"success", "message", "data", "timestamp"} 形状）。

规则：
  - 业务代码只 raise 本文件定义的异常（或让它冒出来的 ValueError，见下）
  - 路由端点不写 try/except 样板，异常由 app 级 handler 统一转 HTTP
  - ValueError = 模板/曲线数据校验错 → 422（沿用现有语义，算法侧 reader/parse 也抛它）
"""
from __future__ import annotations

import logging
import traceback
from datetime import datetime

from fastapi import status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


def make_response(data=None, message: str = "ok", success: bool = True) -> dict:
    """统一响应壳（skill §6）：{"success", "message", "data", "timestamp"}。

    Args:
        data: 结果载荷（任意可 JSON 化结构；失败时为 None）。
        message (str): 给前端的消息；失败时为可直接展示的原因。
        success (bool): True 成功 / False 失败。

    Returns:
        dict: 统一响应体。
    """
    return {"success": success, "message": message, "data": data,
            "timestamp": datetime.now().isoformat(timespec="seconds")}


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
    """BizError/ValueError/请求体校验/未捕获异常 → 语义化状态码 + make_response
    形状错误体（skill §7：所有响应含错误一律统一壳，无 FastAPI 默认体漏网）。

    Args:
        app (FastAPI): 应用实例。

    Returns:
        None
    """
    @app.exception_handler(BizError)
    async def _biz(request, exc: BizError):
        return JSONResponse(status_code=exc.code,
                            content=make_response(message=exc.message, success=False))

    @app.exception_handler(ValueError)
    async def _value(request, exc: ValueError):
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=make_response(message=str(exc), success=False))

    @app.exception_handler(RequestValidationError)   # Pydantic 请求体校验失败（原 FastAPI 默认体）
    async def _req(request, exc: RequestValidationError):
        errs = "; ".join(f"{'.'.join(map(str, e['loc'][1:])) or 'body'}: {e['msg']}"
                         for e in exc.errors()[:3])
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=make_response(message=f"请求参数校验失败: {errs}", success=False))

    @app.exception_handler(Exception)   # 兜底：未捕获异常（原 FastAPI 默认 500 体）
    async def _all(request, exc: Exception):
        logging.getLogger("service").error("未捕获异常: %s\n%s", exc, traceback.format_exc())
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=make_response(message=f"Internal Server Error: {exc}", success=False))
