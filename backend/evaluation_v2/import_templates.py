"""评测数据集导入模板的单一事实源。

模板字段与导入校验器（``evaluation_v2.imports`` / legacy imports）共用同一份
字段口径：改字段先改校验器，再同步这里，避免模板教用户填的内容和校验器
要求的不一致。
"""

from __future__ import annotations

import csv
import io
import json

_TEMPLATE_FORMATS = ("jsonl", "csv", "fields")

_RAG_SAMPLE_CASES = [
    {
        "question": "跨境商品 7 天无理由退货的运费由谁承担？",
        "expected": {
            "behavior": "answer",
            "reference_answer": "非质量问题的 7 天无理由退货运费由买家承担；商品存在质量问题时由商家承担。",
            "required_facts": ["非质量问题运费由买家承担", "质量问题运费由商家承担"],
            "evidence": [
                {
                    "doc_id": "policy-aftersale-014",
                    "quote": "非质量问题退货运费由买家承担；质量问题退货运费由商家承担。",
                }
            ],
        },
        "metadata": {"tier": "core", "query_type": "single_doc", "tags": ["售后"], "splits": ["regression"]},
    },
    {
        "question": "会员积分过期后可以恢复吗？",
        "expected": {
            "behavior": "answer",
            "reference_answer": "积分过期不可恢复，每年 12 月 31 日 24 点清零，清零前 7 天有站内与短信提醒。",
            "required_facts": ["过期不可恢复", "清零前有提醒"],
            "evidence": [
                {"doc_id": "policy-points-003", "quote": "积分过期后不可恢复，每年 12 月 31 日 24 点清零。"}
            ],
        },
        "metadata": {"tier": "core", "query_type": "single_doc", "tags": ["会员"], "splits": ["regression"]},
    },
]

_NON_RAG_SAMPLE_CASES = [
    {
        "id": "cs-001",
        "module": "cs",
        "question": "我要修改收货地址",
        "expected": {
            "behavior": "answer",
            "reference_answer": "出库前可在订单详情自助修改收件人信息。",
            "required_facts": ["出库前", "自助修改"],
        },
        "metadata": {"tier": "core", "query_type": "single_doc", "tags": ["订单"], "splits": ["regression"]},
    },
    {
        "id": "cs-002",
        "module": "cs",
        "question": "订单显示已签收但没收到货怎么办？",
        "expected": {
            "behavior": "answer",
            "reference_answer": "先发起物流核查，核实为丢件后触发退款或补发。",
            "required_facts": ["物流核查", "退款或补发"],
        },
        "metadata": {"tier": "core", "query_type": "single_doc", "tags": ["物流"], "splits": ["regression"]},
    },
]

_FIELDS_DOC = """# 评测数据集字段说明

## 通用字段（两种格式一致）

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| question | 是 | 用户问题，1–12000 字符 |
| expected | 是 | 标准事实（JSON 对象），见下 |
| metadata | 否 | 元数据（JSON 对象）：tier / query_type / tags / splits |
| id | 否 | 用例标识，缺省自动生成；1–160 字符 |
| module | 否 | 所属模块；填写时必须与 manifest.module 一致 |

## expected 的结构

- `behavior`：`answer`（回答题）或 `refuse`（拒答题）。
- 回答题（behavior=answer）：
  - `reference_answer`：参考答案，判分与 RAGAS ground truth 使用。
  - `required_facts`：必需事实列表，回答需覆盖的关键点。
  - `evidence`：证据引用列表（仅 RAG 数据集），每条含 `doc_id` 与 `quote`
    （引用原文）。quote 必须能在知识库快照原文中逐字找到，发布与运行前
    会由服务端核验。
- 拒答题（behavior=refuse）：只能填 `reason`（`no_evidence` 或
  `permission_denied`），不能携带答案、必需事实或证据。

## 文件格式

- `.jsonl`：一行一个用例（RAG 模板为此格式）。
- `.csv`：首行表头，`expected` / `metadata` / `tags` / `splits` 列填 JSON 字符串；
  请用 Excel 另存为「CSV UTF-8」。未通过 `csv_mapping` 映射时，列名必须使用
  标准字段名（id / module / question / expected / metadata / tier / query_type /
  tags / splits / archived 的子集）。
- 单文件 ≤ 5 MB；上传前可在导入向导中先做校验预览，支持跳过错误行。

## 元数据字段

- `tier`：样本分层（smoke / core / hard / regression 等按套件约定）。
- `query_type`：single_doc / multi_hop / adversarial / negative / table 等。
- `tags`：自由标签数组；`splits`：用途切分数组（如 regression）。
"""


def _jsonl_content() -> bytes:
    lines = [json.dumps(case, ensure_ascii=False) for case in _RAG_SAMPLE_CASES]
    return ("\n".join(lines) + "\n").encode("utf-8")


def _csv_content() -> bytes:
    columns = ["id", "module", "question", "expected", "metadata", "tier", "query_type", "tags", "splits"]
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns)
    writer.writeheader()
    for case in _NON_RAG_SAMPLE_CASES:
        writer.writerow({
            "id": case["id"],
            "module": case["module"],
            "question": case["question"],
            "expected": json.dumps(case["expected"], ensure_ascii=False),
            "metadata": json.dumps(case["metadata"], ensure_ascii=False),
            "tier": case["metadata"]["tier"],
            "query_type": case["metadata"]["query_type"],
            "tags": json.dumps(case["metadata"]["tags"], ensure_ascii=False),
            "splits": json.dumps(case["metadata"]["splits"], ensure_ascii=False),
        })
    # UTF-8 BOM：Excel 双击打开不乱码
    return buffer.getvalue().encode("utf-8-sig")


def _validate_module_for_format(module: str, fmt: str) -> None:
    if fmt == "jsonl" and module != "rag":
        raise ValueError("JSONL 模板仅提供 RAG 数据集口径；其他模块请使用 CSV 模板")
    if fmt == "csv" and module == "rag":
        raise ValueError("RAG 数据集请使用 JSONL 模板（CSV 不支持证据引用结构）")


def build_template(module: str, fmt: str) -> tuple[str, bytes, str]:
    """返回 (下载文件名, 内容字节, media_type)。非法组合抛 ValueError。"""
    normalized_module = str(module or "").strip() or "rag"
    normalized_fmt = str(fmt or "").strip().lower()
    if normalized_fmt not in _TEMPLATE_FORMATS:
        raise ValueError(f"不支持的模板格式：{fmt}（支持：{'/'.join(_TEMPLATE_FORMATS)}）")
    _validate_module_for_format(normalized_module, normalized_fmt)

    if normalized_fmt == "jsonl":
        return "rag-dataset-template.jsonl", _jsonl_content(), "application/x-ndjson"
    if normalized_fmt == "csv":
        return f"{normalized_module}-dataset-template.csv", _csv_content(), "text/csv; charset=utf-8"
    return (
        "evaluation-dataset-fields.md",
        _FIELDS_DOC.encode("utf-8"),
        "text/markdown; charset=utf-8",
    )
