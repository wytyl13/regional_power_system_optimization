#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""接口测试 1/2：POST /api/datasets 上传模版 → 校验返回的 datasetId 等字段。

用法（项目根目录）：
    python tests/test_api_upload.py <模版.xlsx 路径>   # 缺省 海南2030算例_20261012/数据模版-海南.xlsx
须先起服务：service/ 下 `python -m uvicorn app:app --port 8000`
"""
import json
import sys
import urllib.request
import uuid
from pathlib import Path

BASE = "http://127.0.0.1:8000"
HERE = Path(__file__).resolve().parent


def upload(path: Path) -> dict:
    """multipart/form-data 手工拼装（环境无 requests，用标准库）。"""
    boundary = "----" + uuid.uuid4().hex
    filename = path.name
    # 中文文件名按请求头声明的 UTF-8 编码传输（RFC 7578 §4.2 filename*= 形式更严，
    # 但 FastAPI 对 UTF-8 的 filename 字段本身就能正确解码）
    body = (
        f"--{boundary}\r\n"
        f"Content-Disposition: form-data; name=\"file\"; "
        f"filename=\"{filename}\"\r\n"
        f"Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet\r\n\r\n"
    ).encode("utf-8") + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode("utf-8")
    req = urllib.request.Request(
        BASE + "/api/datasets", data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    xlsx = Path(sys.argv[1]) if len(sys.argv) > 1 else \
        HERE.parent / "海南2030算例_20261012" / "数据模版-海南.xlsx"
    if not xlsx.exists():
        print(f"模版不存在: {xlsx}")
        return 1
    print(f"上传: {xlsx.name}（%.1f KB）" % (xlsx.stat().st_size / 1024))
    out = upload(xlsx)
    # 断言契约（app.py /api/datasets 返回七字段；warnings/excludedUnits 可为空列表）
    expect = {"datasetId", "area", "year", "dataTreeModels", "warnings",
              "configDefaults", "fieldsMapping", "excludedUnits"}
    missing = expect - set(out)
    assert not missing, f"返回缺字段: {missing}"
    assert out["datasetId"].startswith("ds_"), out["datasetId"]
    assert out["year"] == 2030 and out["area"] == "海南", (out["area"], out["year"])
    assert out["dataTreeModels"], "dataTreeModels 为空"
    print("datasetId :", out["datasetId"])
    print("地区/年份:", out["area"], out["year"])
    print("warnings :", len(out["warnings"]), "条")
    print("excluded :", len(out["excludedUnits"]), "台")
    print("==== 上传接口 PASS ====")
    # datasetId 丢给下一个脚本用；人工跑时可复制控制台这行
    print(json.dumps({"datasetId": out["datasetId"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except urllib.error.HTTPError as e:      # 422 校验失败也要能看见报文
        print("HTTP %d: %s" % (e.code, e.read().decode("utf-8")))
        raise SystemExit(1)
