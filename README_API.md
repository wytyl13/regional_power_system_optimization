# 背景

> 该项目聚焦于课题1（电力系统规划）的自动求解模式，不依赖具体的模型规则和扣减顺序。输入数据包含：课题1的输入（通过excel表格）和课题2独有的成本参数（系统返回默认值，前端支持用户修改），前端需要渲染这些输入数据。

> **包含如下电源：**
>
> 1. 煤电
> 2. 气电
> 3. 生物质
> 4. 核电
> 5. 常规水电：径流式水电和库容式水电
> 6. 风电
> 7. 光伏
> 8. 光热
> 9. 新能源：包含风电、光伏和新能源友好电站
> 10. 储能：包括电化学储能和抽水蓄能

> **输出数据包含：**
>
> - 设备运行逐小时电力：全省功率平衡分析，单位是功率
> - 成本构成分析：分别按照目标函数和成本性质汇总，单位为万元
> - 电量与功率特征分析：主要包含全周期各个电源的出力电量（单位万MWh）和最大出力（单位MW）
> - 火电运行与启停数据：主要包含煤电和气电启停特征
> - 风电与光伏消纳特征：主要包含风电和光伏的可用电量、已用电量、弃用电量（MWh）和利用率
> - 抽水蓄能运行特征：
> - 独立电化学储能运行特征：

# 接口

> http://192.168.2.40:9999/

## POST /api/datasets

> **传参**
> file: binary #上传二进制模板文件（可选）

> **return: 传递file**
```json
{
  "datasetId": "ds_9bfeec0820e3",
  "area": "海南",
  "year": 2030,
  "categories": [
    {
      "name": "",
      "items": [
        {
          "name": "",
          "tables": [
            {
              "name": "",
              "columns": [],
              "rows": [
                {}
              ]
            }
            // ... 
          ],
          "curve": {
            "sheet": "负荷-8760",
            "filterBy": [
              "区域"
            ],
            "match": "exact"
          }
        }
      ]
    }
    // ...
  ], // 需要在前端展示的用户上传的模板数据，有表格的需要渲染表格
  "config": {}, // 需要在前端展示的基础配置，支持用户修改
  "fixedParams": [] // 需要在前端展示的基础配置，不支持用户修改
}
```


> **return: 不传递file**
```json
{
	"config": {}
}
```


## GET /api/datasets//curves/

> 曲线懒加载（按 filterBy 键值筛，需要在前端支持表格查看和导出

**示例：** http://192.168.2.40:9999/api/datasets/<id></id>/curves/负荷-8760?区域=海南"

> **return**
```json
{
	"columns": [],
	"rows": []
}
```

## POST /api/jobs

> 提交计算（datasetId + config）→ jobId

> **传参**
```json
{
	"datasetId": "", # /api/datasets的返回值datasetId
	"config": {
		"solver": {
			"mipGap": 0.9
		}
	}, # 用户修改后的基础配置，按照原格式简化后的字典传递

}
```

> **return**
```json
{
	"jobId": "job_20261013_145202_c677c8"
}
```

## GET /api/jobs

> 查看任务列表

> **return**

```json
 {
	"jobs": [
		{
			"jobId": "job_20261013_145202_c677c8",
			"datasetId": "ds_e087562f6438",
			"status": "running",
			"submittedAt": "2026-10-13 14:52:02"
		},
		{
			"jobId": "job_20261013_122817_9a2f4f",
			"datasetId": "ds_a7b6117e21d0",
			"status": "solved",
			"submittedAt": "2026-10-13 12:28:17",
			"finishedAt": "2026-10-13 12:28:29",
			"area": "海南",
			"year": 2030,
			"scenario": {
				"hours": 24,
				"startTime": "2030-01-01 00:00",
				"hydrology": "平水年",
				"interprovincial": false,
				"interRegionTransfer": false
			},
			"fourItems": {
				"variables": {
					"count": 8007,
					"binary": 2712
				},
				"constraints": {
					"rows": 13384,
					"nonzeros": 37831
				},
				"status": "OPTIMAL",
				"objective": {
					"value": 43109003.31607772,
					"mipGapPct": 0.09770398377554372,
					"runtime_s": 2.1649999618530273
				}
			}
		},
		...
	]
}
```

## GET /api/jobs/

> 查看指定任务

> **return**
```json
{

    "jobId": "job_20261013_145202_c677c8",
    "datasetId": "ds_e087562f6438",
    "status": "running",
    "submittedAt": "2026-10-13 14:52:02",
    "startedAt": "2026-10-13 14:52:02"

}
```

## GET /api/jobs/results

> 查看指定任务结果

**示例1：** http://127.0.0.1:8000/api/jobs/job_20261013_145202_c677c8/results

> **return**
```json
{
	"detail": "任务状态 running，尚无结果"
}
```

**示例2：** http://127.0.0.1:8000/api/jobs/job_20261013_122817_9a2f4f/results

> **return**

```json
{
	"jobId": "job_20261013_122817_9a2f4f",
	"datasetId": "ds_a7b6117e21d0",
	"area": "海南",
	"year": 2030,
	"submittedAt": "2026-10-13 12:28:17",
	"finishedAt": "2026-10-13 12:28:29",
	"result": {
		"config": {},
		"views": {
			"deviceOperation": {
				"title": "", # 数据名称
				"panels": [] # 需要绘制的曲线图像
			},
			... # views下的其它需要渲染的图像
		}
	}
}
```
