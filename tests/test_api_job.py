#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/13 08:36
@Author  : weiyutao
@File    : test_api_job.py

接口测试 2/2：上传模版 → 提交计算 → 轮询到 solved → 结果写入本地 JSON。

用法（项目根目录）：
    python tests/test_api_job.py <模版.xlsx> [小时数] [输出.json]
缺省：数据模版-海南.xlsx、24 小时、tests/tmp_api_result.json
须先起服务：service/ 下 `python -m uvicorn app:app --port 8000`
"""
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

BASE = "http://127.0.0.1:8000"
HERE = Path(__file__).resolve().parent
DEFAULT_XLSX = HERE.parent / "海南2030算例_20261012" / "数据模版-海南.xlsx"


def post(path: str, payload: dict) -> dict:
    """POST JSON 并解析响应（标准库 urllib）。

    Args:
        path (str): API 路径（不含 BASE）。
        payload (dict): 请求体。

    Returns:
        dict: 响应 JSON。
    """
    req = urllib.request.Request(
        BASE + path, data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get(path: str) -> dict:
    """GET 并解析响应。

    Args:
        path (str): API 路径（不含 BASE）。

    Returns:
        dict: 响应 JSON。
    """
    with urllib.request.urlopen(BASE + path, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def upload_dataset(xlsx: Path) -> str:
    """multipart 手工拼装（标准库，无 requests 依赖）。

    Args:
        xlsx (Path): 模版文件路径。

    Returns:
        str: datasetId。
    """
    boundary = "----" + uuid.uuid4().hex
    body = (
        f"--{boundary}\r\n"
        f"Content-Disposition: form-data; name=\"file\"; "
        f"filename=\"{xlsx.name}\"\r\n"
        f"Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet\r\n\r\n"
    ).encode("utf-8") + xlsx.read_bytes() + f"\r\n--{boundary}--\r\n".encode("utf-8")
    req = urllib.request.Request(
        BASE + "/api/datasets", data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))["data"]["datasetId"]


def main() -> int:
    """上传模版 → 提交计算 → 轮询到终态 → 四项核对 + 瘦身契约断言 → 落盘结果。

    Returns:
        int: 0 = PASS，1 = 失败。
    """
    xlsx = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_XLSX
    hours = int(sys.argv[2]) if len(sys.argv) > 2 else 24
    out_path = Path(sys.argv[3]) if len(sys.argv) > 3 \
        else HERE / "tmp_api_result.json"

    ds_id = upload_dataset(xlsx)
    print(f"上传 OK: datasetId={ds_id}")
    job = post("/api/jobs", {"datasetId": ds_id,
                             "config": {"scenario": {"hours": hours}}})
    job_id = job["data"]["jobId"]
    print(f"提交 OK: jobId={job_id}（{hours}h，单队列串行，T24 约半分钟）")

    while True:                                   # 轮询到终态（queued→running→solved/failed）
        time.sleep(5)
        st = get(f"/api/jobs/{job_id}")["data"]
        print(f"  [{time.strftime('%H:%M:%S')}] {st['status']}")
        if st["status"] == "solved":
            break
        if st["status"] == "failed":
            print(f"失败: {st.get('error')}")
            return 1

    four = st["fourItems"]                        # 黄金规则：先核对四项再谈结论
    assert four["constraints"]["rows"] > 0 and four["objective"]["value"] is not None, four
    print("四项核对: 变量 %d（binary %d）/ 约束 %d / %s / obj %.2f 元" % (
        four["variables"]["count"], four["variables"]["binary"],
        four["constraints"]["rows"], four["status"],
        four["objective"]["value"]))

    result = get(f"/api/jobs/{job_id}/results")["data"]
    # v3 瘦身契约（2026-10-20 定稿）：顶层只剩任务标识 + result
    assert set(result) == {"jobId", "datasetId", "area", "year",
                           "submittedAt", "finishedAt", "result"}, set(result)
    r = result["result"]
    assert set(r) == {"report", "config", "views"}, set(r)
    # views 契约（2026-10-21 定稿）：list，sheet_name/index 组级共享 + *_data 列集
    v = r["views"]
    assert isinstance(v, list) and len(v) == 1, v
    blk = v[0]
    assert blk["sheet_name"] == "逐小时结果", blk
    assert blk["index"], blk
    for part in ("series_data", "lines_data"):
        assert blk[part], part
        for col in blk[part]:
            assert set(col) == {"column_name", "data"}, col
            assert len(col["data"]) == len(blk["index"]), col["column_name"]
    names = {c["column_name"] for c in blk["series_data"]}
    assert names == {"煤电", "气电", "生物质", "核电", "径流式水电", "库容式水电",
                     "风电", "光伏", "抽水蓄能", "电化学储能", "友好电站"}, names
    lines = {c["column_name"] for c in blk["lines_data"]}
    assert lines == {"原始负荷", "响应后负荷"}, lines
    # config 块：与 /api/datasets 下发同款数组树
    assert isinstance(r["config"], list), type(r["config"])
    assert r["config"][0]["group_name"] == "scenario", r["config"][0]
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    print("已写入: %s（%.1f KB）" % (out_path, out_path.stat().st_size / 1024))
    print("==== 计算接口 PASS ====")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except urllib.error.HTTPError as e:      # 422 校验失败也要能看见报文
        print("HTTP %d: %s" % (e.code, e.read().decode("utf-8")))
        raise SystemExit(1)
