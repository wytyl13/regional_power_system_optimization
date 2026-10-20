#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/10/13 08:36
@Author  : weiyutao
@File    : test_api_upload.py

接口测试 1/2：POST /api/datasets 上传模版 → 校验新契约（categories/config/fixedParams）。

用法（项目根目录）：
    python tests/test_api_upload.py <模版.xlsx 路径>   # 缺省 海南2030算例_20261012/数据模版-海南.xlsx
须先起服务：service/ 下 `python -m uvicorn app:app --port 8000`
旧树契约 dataTreeModels/fieldsMapping 已废弃（见 docs/temp/服务接口设计方案.md §3）。
"""
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

BASE = "http://127.0.0.1:8000"
HERE = Path(__file__).resolve().parent


def upload(path: Path) -> dict:
    """multipart/form-data 手工拼装（环境无 requests，用标准库）。

    Args:
        path (Path): 模版文件路径。

    Returns:
        dict: 上传端点响应 JSON。
    """
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


def get(path: str) -> dict:
    """path 含中文（sheet 名/query 值），逐段 percent-encode 后请求。
    服务端 FastAPI/Starlette 自动解码回 str。

    Args:
        path (str): API 路径（不含 BASE，可带 query）。

    Returns:
        dict: 响应 JSON。
    """
    base, _, q = path.partition("?")
    url = "/".join(urllib.parse.quote(seg, safe="") for seg in base.split("/"))
    if q:
        url += "?" + "&".join(
            "=".join(urllib.parse.quote(x, safe="") for x in kv.split("=", 1))
            for kv in q.split("&"))
    with urllib.request.urlopen(BASE + url, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    """上传模版并校验新契约：八字段/categories 五大类/曲线懒加载/config 表单/fixedParams。

    Returns:
        int: 0 = PASS，1 = 模版不存在或断言失败。
    """
    xlsx = Path(sys.argv[1]) if len(sys.argv) > 1 else \
        HERE.parent / "海南2030算例_20261012" / "数据模版-海南.xlsx"
    if not xlsx.exists():
        print(f"模版不存在: {xlsx}")
        return 1
    print(f"上传: {xlsx.name}（%.1f KB）" % (xlsx.stat().st_size / 1024))
    out = upload(xlsx)

    # ---- 契约：八字段 ----
    expect = {"datasetId", "area", "year", "categories", "config",
              "fixedParams", "warnings", "excludedUnits"}
    missing = expect - set(out)
    assert not missing, f"返回缺字段: {missing}"
    assert out["datasetId"].startswith("ds_"), out["datasetId"]
    assert out["year"] == 2030 and out["area"] == "海南", (out["area"], out["year"])

    # ---- categories：五大类固定、顺序即渲染序；细分/curve 元数据齐 ----
    cats = out["categories"]
    assert [c["name"] for c in cats] == ["负荷及备用", "电源", "送电协议",
                                         "一体化电站", "检修安排"], cats
    by_name = {c["name"]: c for c in cats}
    power = by_name["电源"]
    assert [i["name"] for i in power["items"]] == \
        ["核电", "抽水蓄能", "常规水电", "气电", "生物质", "煤电", "风电", "光伏", "新型储能"]
    # 每个细分：tables 内联 {name,columns,rows} 值字符串；curve 只给元数据
    n_tables = 0
    for c in cats:
        items = c.get("items")
        if items is None:                       # 送电协议：本级挂表
            assert c.get("tables") or "curve" in c, c["name"]
            n_tables += len(c.get("tables", []))
            continue
        for it in items:
            t = it["tables"][0]
            assert t["columns"] and all(isinstance(v, str)
                                         for r in t["rows"] for v in r.values()), it["name"]
            n_tables += len(it["tables"])
            assert set(it.get("curve", {"sheet": "x"})["sheet"]) != set()
    curves = {it["name"]: it["curve"]
              for c in cats for it in c.get("items", []) if "curve" in it}
    assert set(curves) == {"负荷", "常规水电", "风电", "光伏"}, sorted(curves)
    assert by_name["送电协议"]["curve"]["filterBy"] == ["区域", "通道名称", "送端", "受端"]
    assert curves["光伏"].get("wholeSheet") is True
    print("categories OK: 5 类 21 表；细分曲线 =", sorted(curves))

    # ---- curve 元数据真实可用：懒加载 elect 城 站点名走一遍 ----
    wind_curve = curves["风电"]
    ds_id = out["datasetId"]
    wind_tbl = next(it["tables"][0] for it in power["items"] if it["name"] == "风电")
    station = wind_tbl["rows"][0]["电站名称"]
    q = "&".join(f"{k}={station}" for k in wind_curve["filterBy"])
    cv = get(f"/api/datasets/{ds_id}/curves/{wind_curve['sheet']}?{q}")
    assert len(cv["index"]) == 365 and len(cv["data"]) == 24, (len(cv["index"]), len(cv["data"]))
    assert cv["sheet_name"] == wind_curve["sheet"]
    assert isinstance(cv["data"][0]["data"][0], (int, float))   # 数值不转字符串
    assert not {"区域", "电站名称"} & {d["column_name"] for d in cv["data"]}   # 标识列已剔
    print(f"曲线懒加载 OK: 风电[{station}] → 365 行")
    pv = get(f"/api/datasets/{ds_id}/curves/光伏-出力曲线")
    assert len(pv["index"]) == 365, len(pv["index"])
    # 0 命中 → 422
    try:
        get(f"/api/datasets/{ds_id}/curves/常规水电-出力曲线?电站名称=不存在")
        raise AssertionError("0 命中应 422")
    except urllib.error.HTTPError as e:
        assert e.code == 422, e.code
    print("0 命中 422 OK")

    # ---- config：{组:{键:{label,value[,options]}}}，收资浮出 ----
    cfg = out["config"]
    for g, kv in cfg.items():
        for k, leaf in kv.items():
            assert leaf["label"] and "value" in leaf, (g, k)
    assert cfg["scenario"]["hydrology"]["options"] == ["平水年", "枯水年"]
    assert cfg["province"]["hotReserveRatio"]["value"] == 0.1
    assert cfg["demandResponse"]["shiftRatio"]["value"] == 0.025
    print("config OK: 组=%d 字段=%d" % (len(cfg),
                                       sum(len(v) for v in cfg.values())))
    assert out["fixedParams"] and all("key" in f and "label" in f for f in out["fixedParams"])
    print("fixedParams OK:", len(out["fixedParams"]), "条")
    print("warnings :", len(out["warnings"]), "条")
    print("excluded :", len(out["excludedUnits"]), "台")
    print("==== 上传接口 PASS ====")
    print(json.dumps({"datasetId": out["datasetId"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except urllib.error.HTTPError as e:      # 422 校验失败也要能看见报文
        print("HTTP %d: %s" % (e.code, e.read().decode("utf-8")))
        raise SystemExit(1)
