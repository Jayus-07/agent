# -*- coding: utf-8 -*-
"""R3 探路版测试集生成器（rag_100_docs）

用途：
  生成 27 份多格式语料 fixtures（PDF 文本版 / 扫描件图版 / DOCX / MD / TXT / XLSX / CSV，
  含 2 份相似文档与 1 组同政策三版本）+ backend/evaluation/datasets/rag_100_docs.json
  （任务书 §4 8 个标注字段：doc_id / expected_doc_ids / expected_chunk_ids /
  关键事实 / 查询类型 / 是否拒答 / 版本要求 / 权限范围），并做一致性自校验。

复现：
  ./.venv/Scripts/python.exe backend/evaluation/fixtures/rag_100_docs/generate_fixtures.py

边界：
  仅生成内容与 JSON，不修改 backend 其他代码；expected_chunk_ids 在索引完成前不可知，
  以 null + expected_chunk_anchors（原文锚点）表示，索引后回填。

关键事实在生成期即固化在源文件中：
  - 文本类文件直接写入事实文本；
  - 扫描件 PDF 由文字渲染成图像封装（无需真实 OCR，OCR 回填结果应与 key_facts 一致）。
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parents[2]                # backend/
REPO = BACKEND.parent                    # 仓库根
DATASET_PATH = BACKEND / "evaluation" / "datasets" / "rag_100_docs.json"
FILES_DIR = HERE / "files"

FONT_DIR = Path("C:/Windows/Fonts")
FONT_HEI = str(FONT_DIR / "simhei.ttf")

KB_ID = "rag_100_docs"
COMPANY = "星辰云科技有限公司"

# ---------------------------------------------------------------- MD / TXT 源文本

MD_ATTENDANCE_RND = """# 考勤管理制度（研发中心）

制度编号：XC-HR-2024-017
发布部门：人力资源部
生效日期：2024-04-01

## 1. 工作时间
研发中心实行弹性打卡制度，弹性区间为 **09:30 至 10:00**，日标准工时 8 小时。

## 2. 迟到处理
月内迟到 **3 次以内（含 3 次）且每次不超过 10 分钟** 的，不计入考勤处罚；
超出部分每次扣减绩效分 0.5 分。

## 3. 加班管理
加班须提前在 OA 系统提交审批，加班时长 **满 2 小时起算**，未满 2 小时按 0 计。
经审批的加班时长可按 1:1 折算调休。

## 4. 调休有效期
年度调休须在当年 12 月 31 日前使用完毕，过期自动清零，不折现。
"""

MD_ATTENDANCE_MKT = """# 考勤管理制度（市场部）

制度编号：XC-HR-2024-018
发布部门：人力资源部
生效日期：2024-04-01

## 1. 工作时间
市场部实行弹性打卡制度，弹性区间为 **08:30 至 09:00**，日标准工时 8 小时。

## 2. 迟到处理
月内迟到 **5 次以内（含 5 次）且每次不超过 15 分钟** 的，不计入考勤处罚；
超出部分每次扣减绩效分 0.5 分。

## 3. 加班管理
加班须提前在 OA 系统提交审批，加班时长 **满 1.5 小时起算**，未满 1.5 小时按 0 计。
经审批的加班时长可按 1:1 折算调休。

## 4. 调休有效期
年度调休须在当年 12 月 31 日前使用完毕，过期自动清零，不折现。
"""

MD_SEAL = """# 印章使用管理办法

制度编号：XC-XZ-2023-009
发布部门：行政部
生效日期：2023-07-01

## 1. 印章保管
公司公章、合同专用章由 **行政部** 指定两名专员双人分管，任何个人不得单独携带公章外出。

## 2. 用印审批
一般文件用印须经部门负责人审批；**合同类文件用印须先经法务部会签**，再报分管副总裁批准。

## 3. 用印登记
每次用印须在《用印登记簿》登记编号、事由、经办人，登记记录保存 **5 年**。
"""

MD_FAQ_EMPLOYEE = """# 员工常见问题 FAQ（行政版）

## 访客 Wi-Fi
访客 Wi-Fi 名称 **XC-Guest**，密码每月 1 日由 IT 部更新并在前台公告。

## 费用报销
报销单提交后由部门负责人与财务部两级审核，审核周期为 **10 个工作日内**。

## 工牌补办
工牌遗失后请到行政部挂失，补办周期 **3 个工作日**，补办费 **20 元**。

## 班车线路
公司班车共 3 条线路：1 号线（金山公交站，07:20 发车）、2 号线（东湖苑，07:35 发车）、
3 号线（软件园南门，07:50 发车）。

## 年度体检
年度体检每年 9 月至 10 月集中安排，具体时间由 HR 通知各部门。
"""

MD_ONBOARDING = """# 新员工入职手册

## 1. 入职材料
入职当日请携带：身份证复印件、学历学位证书、离职证明、体检报告、一寸照片 2 张。

## 2. 试用期
试用期 **6 个月**，试用期考核由直属上级与 HRBP 共同评定。

## 3. 导师制
每位新员工配备导师 1 名，导师辅导期为 **3 个月**。

## 4. 转正
转正答辩由部门组织，**需提前 5 个工作日** 在 OA 上预约答辩时间。
"""

MD_INFOSEC = """# 信息安全管理制度

发布部门：数据安全委员会
生效日期：2025-01-01

## 1. 数据密级
公司数据分为 **public（公开）、internal（内部）、confidential（机密）** 三级，
所有文档默认按 internal 处理。

## 2. 数据导出
内部及机密数据的对外导出须获得 **数据保护官（DPO）书面审批**，审批记录留存 3 年。

## 3. 权限回收
员工离岗当日由 IT 部回收全部系统权限，账号保留 90 天后注销。
"""

MD_CONTRACT_BLUEWHALE = """# 数据服务合同（星辰云 × 蓝鲸数据）

合同编号：XC-HT-2025-041

甲方：{c}
乙方：蓝鲸数据（厦门）信息技术有限公司

## 1. 服务内容
乙方为甲方提供客户画像标签数据服务及 API 接口运维支持。

## 2. 合同金额
合同总金额为 **人民币 1,200,000 元（120 万元）**，按季度分 8 期支付。

## 3. 服务期限
服务期为 **2025 年 3 月 1 日至 2027 年 2 月 28 日**。

## 4. 违约责任
乙方逾期交付的，每逾期一日按月服务费的 **5%** 支付违约金；累计逾期超过 30 日，
甲方有权解除合同。
""".format(c=COMPANY)

MD_REPORT_CS = """# 客服运营年度报告（2025）

## 总体表现
2025 年全年工单总量 **58,320 件**，客户满意度 **92.4%**。

## 响应时效
人工平均首次响应时长 **45 秒**，机器人接待占比 63%。

## 改进方向
2026 年重点压缩夜间时段响应时长，目标平均首次响应进入 40 秒以内。
"""

MD_TRAVEL_V1 = """# 差旅费管理办法（V1）

制度编号：XC-HR-2024-032
发布日期：2024-03-15
生效日期：2024-04-01

## 1. 住宿标准
- 一线城市（北京/上海/广州/深圳）：**500 元/晚**
- 二线城市：**380 元/晚**

## 2. 交通
高铁二等座、经济舱凭票实报实销。

## 3. 发票
凭纸质增值税发票报销。
"""

TXT_FAQ_OPS = """运维值班 FAQ（IT 部）
=====================

1. 7x24 值班电话：0591-8888-6677
2. 故障分级响应：
   - P1 级（核心服务不可用）：15 分钟内响应
   - P2 级（部分功能受损）：30 分钟内响应
   - P3 级（体验类问题）：2 小时内响应
3. 升级路径：值班工程师 -> 值班经理 -> CTO（故障 30 分钟未升级自动上报）。
4. 值班交接：每日 10:00 在运维群交接值班记录。
"""

TXT_NOTES_REVIEW = """产品评审会议纪要（2026-08-30）
=================================

参会：产品部、研发中心、市场部负责人

决议一：星辰会议助手 v1.2 排期 2026 年 10 月上线，具体发布日期待 9 月评审确定。
决议二：会议助手项目 Q4 预算追加 35 万元，用于语音转写算力扩容。
决议三：项目由苏黎负责，双周向产品委员会汇报进度。
"""

TXT_DICT_CUSTOMER = """数据字典 · 客户域（v2026.06）
==============================

c_user_id    客户唯一标识，格式：CU + 8 位数字（示例：CU00318765）。
c_tier       客户等级，枚举 A / B / C。
c_created_at 开户时间，东八区（UTC+8），ISO8601 格式。
c_channel    获客渠道，枚举 direct / partner / ads。
"""

# ---------------------------------------------------------------- dataset 用例

def A(doc_id, doc_ids, key_facts, qtype, refuse=False, reason=None,
      chunk_ids=None, anchors=None, vreq=None, perms=("general",)):
    return {
        "doc_id": doc_id,
        "expected_doc_ids": list(doc_ids),
        "expected_chunk_ids": chunk_ids,
        "expected_chunk_anchors": anchors or [],
        "key_facts": key_facts,
        "query_type": qtype,
        "should_refuse": refuse,
        "refusal_reason": reason,
        "version_requirement": vreq or {"type": "any"},
        "permission_scope": list(perms),
    }

def M(fmt, doctype, difficulty="easy", no_answer=False, group=None):
    md = {"format": fmt, "doc_type": doctype, "difficulty": difficulty,
          "is_no_answer": no_answer, "source": "generated",
          "schema_version": "1.0-probe"}
    if group:
        md["group"] = group
    return md

VREQ_AS_OF_202508 = {"type": "as_of", "date": "2025-08-01"}
VREQ_AS_OF_202406 = {"type": "as_of", "date": "2024-06-01"}
VREQ_CURRENT = {"type": "current"}
VREQ_CHAIN = {"type": "all_versions",
              "supersedes_chain": ["policy_travel_v1", "policy_travel_v2", "policy_travel_v3"]}

CASES = [
    # ---------------- faq ----------------
    {"id": "RD-001", "kb_id": KB_ID, "module": "rag",
     "question": "公司访客 Wi-Fi 的名称是什么？",
     "annotation": A("faq_employee", ["faq_employee"], ["XC-Guest", "密码每月1日更新"], "faq",
                     anchors=["访客 Wi-Fi 名称 XC-Guest"]),
     "metadata": M("md", "faq", group=None)},
    {"id": "RD-002", "kb_id": KB_ID, "module": "rag",
     "question": "费用报销审核需要多长时间？",
     "annotation": A("faq_employee", ["faq_employee"], ["10个工作日内"], "faq"),
     "metadata": M("md", "faq")},
    {"id": "RD-003", "kb_id": KB_ID, "module": "rag",
     "question": "工牌遗失了怎么补办？费用多少？",
     "annotation": A("faq_employee", ["faq_employee"], ["3个工作日", "补办费20元"], "faq"),
     "metadata": M("md", "faq", difficulty="easy")},
    {"id": "RD-004", "kb_id": KB_ID, "module": "rag",
     "question": "运维值班电话是多少？P1 级故障要求多久内响应？",
     "annotation": A("faq_ops", ["faq_ops"], ["0591-8888-6677", "15分钟内响应"], "faq"),
     "metadata": M("txt", "faq")},

    # ---------------- exact_id ----------------
    {"id": "RD-005", "kb_id": KB_ID, "module": "rag",
     "question": "合同编号 XC-HT-2025-041 的合同金额和服务期限分别是什么？",
     "annotation": A("contract_bluewhale", ["contract_bluewhale"],
                     ["120万元", "2025年3月1日至2027年2月28日"], "exact_id",
                     anchors=["合同编号：XC-HT-2025-041"]),
     "metadata": M("md", "contract")},
    {"id": "RD-006", "kb_id": KB_ID, "module": "rag",
     "question": "资产编号 ZC-2021-118 的设备报废申请，申请人是谁？审批何时完成？",
     "annotation": A("scan_asset_disposal", ["scan_asset_disposal"],
                     ["陈斌", "2026-06-12"], "exact_id"),
     "metadata": M("pdf", "form", difficulty="medium", group="scanned")},
    {"id": "RD-007", "kb_id": KB_ID, "module": "rag",
     "question": "制度编号 XC-XZ-2023-009 对应哪份制度？公章由哪个部门保管？",
     "annotation": A("policy_seal", ["policy_seal"], ["印章使用管理办法", "行政部"], "exact_id"),
     "metadata": M("md", "policy")},
    {"id": "RD-008", "kb_id": KB_ID, "module": "rag",
     "question": "数据字典中 c_user_id 字段的格式是什么？",
     "annotation": A("dict_customer", ["dict_customer"], ["CU+8位数字"], "exact_id"),
     "metadata": M("txt", "reference")},

    # ---------------- multi_condition ----------------
    {"id": "RD-009", "kb_id": KB_ID, "module": "rag",
     "question": "研发中心员工月度迟到几次以内不处罚？加班时长从多久起算？",
     "annotation": A("policy_attendance_rnd", ["policy_attendance_rnd"],
                     ["3次以内含3次且每次不超过10分钟", "满2小时起算"], "multi_condition"),
     "metadata": M("md", "policy", difficulty="medium")},
    {"id": "RD-010", "kb_id": KB_ID, "module": "rag",
     "question": "市场部考勤的弹性打卡区间和迟到免罚次数是多少？",
     "annotation": A("policy_attendance_mkt", ["policy_attendance_mkt"],
                     ["08:30至09:00", "5次以内含5次且每次不超过15分钟"], "multi_condition"),
     "metadata": M("md", "policy", difficulty="medium", group="similar_pair")},
    {"id": "RD-011", "kb_id": KB_ID, "module": "rag",
     "question": "会议室通过什么渠道预订？单次最长可订多久？",
     "annotation": A("manual_meetingroom", ["manual_meetingroom"],
                     ["星辰办公App", "单次最长3小时"], "multi_condition"),
     "metadata": M("docx", "manual")},
    {"id": "RD-012", "kb_id": KB_ID, "module": "rag",
     "question": "供应商准入的综合评分门槛是多少？年审安排在每年几月？",
     "annotation": A("policy_supplier", ["policy_supplier"],
                     ["综合评分80分及以上", "每年3月年审"], "multi_condition"),
     "metadata": M("docx", "policy", difficulty="medium")},

    # ---------------- table_value ----------------
    {"id": "RD-013", "kb_id": KB_ID, "module": "rag",
     "question": "产品企业版的年费是多少？包含多少席位？",
     "annotation": A("table_pricing", ["table_pricing"], ["12,999元/年", "席位不限"], "table_value"),
     "metadata": M("xlsx", "table")},
    {"id": "RD-014", "kb_id": KB_ID, "module": "rag",
     "question": "2026 年第二季度研发部的人力成本是多少？",
     "annotation": A("table_hrcost_2026q2", ["table_hrcost_2026q2"],
                     ["486.5万元"], "table_value", perms=("hr_confidential",)),
     "metadata": M("xlsx", "table", group=None)},
    {"id": "RD-015", "kb_id": KB_ID, "module": "rag",
     "question": "资产编号 SL-0031 的服务器部署在哪个机房？",
     "annotation": A("table_servers", ["table_servers"], ["A1机房"], "table_value",
                     perms=("it_admin",)),
     "metadata": M("xlsx", "table")},
    {"id": "RD-016", "kb_id": KB_ID, "module": "rag",
     "question": "2026 年 6 月华东区销售额是多少？",
     "annotation": A("table_sales_2026", ["table_sales_2026"], ["168.4万元"], "table_value"),
     "metadata": M("csv", "table")},
    {"id": "RD-017", "kb_id": KB_ID, "module": "rag",
     "question": "2026 年 6 月客服工单解决率和平均响应时长分别是多少？",
     "annotation": A("table_tickets_2026", ["table_tickets_2026"],
                     ["93.8%", "41秒"], "table_value"),
     "metadata": M("csv", "table", difficulty="medium")},

    # ---------------- cross_doc ----------------
    {"id": "RD-018", "kb_id": KB_ID, "module": "rag",
     "question": "蓝鲸数据服务合同的合同金额和办公室租赁的月租金分别是多少？",
     "annotation": A("contract_bluewhale", ["contract_bluewhale", "contract_office_lease"],
                     ["120万元", "18.5万元"], "cross_doc", perms=("general", "finance_restricted")),
     "metadata": M("md", "contract", difficulty="hard")},
    {"id": "RD-019", "kb_id": KB_ID, "module": "rag",
     "question": "差旅住宿一线城市标准从 V1 至今调整过几次？当前标准是多少？",
     "annotation": A("policy_travel_v3", ["policy_travel_v1", "policy_travel_v2", "policy_travel_v3"],
                     ["调整过2次", "当前700元/晚"], "cross_doc", vreq=VREQ_CHAIN),
     "metadata": M("pdf", "policy", difficulty="hard", group="version_chain")},
    {"id": "RD-020", "kb_id": KB_ID, "module": "rag",
     "question": "2026 年上半年公司毛利率是多少？6 月华东区销售额是多少？",
     "annotation": A("report_fin_h1_2026", ["report_fin_h1_2026", "table_sales_2026"],
                     ["61.2%", "168.4万元"], "cross_doc",
                     perms=("general", "finance_restricted")),
     "metadata": M("pdf", "report", difficulty="hard")},

    # ---------------- low_confidence ----------------
    {"id": "RD-021", "kb_id": KB_ID, "module": "rag",
     "question": "转正答辩需要提前多久预约？",
     "annotation": A("manual_onboarding", ["manual_onboarding"], ["提前5个工作日"], "low_confidence",
                     anchors=["需提前 5 个工作日 在 OA 上预约答辩时间"]),
     "metadata": M("md", "manual", difficulty="hard")},
    {"id": "RD-022", "kb_id": KB_ID, "module": "rag",
     "question": "投影仪 HDMI 转接头在哪里可以借到？",
     "annotation": A("manual_meetingroom", ["manual_meetingroom"], ["B1层前台"], "low_confidence"),
     "metadata": M("docx", "manual", difficulty="hard")},

    # ---------------- 版本要求（as_of / current）----------------
    {"id": "RD-023", "kb_id": KB_ID, "module": "rag",
     "question": "按 2025 年 8 月执行的差旅制度，一线城市住宿标准是多少？",
     "annotation": A("policy_travel_v2", ["policy_travel_v2"], ["600元/晚"], "multi_condition",
                     vreq=VREQ_AS_OF_202508),
     "metadata": M("docx", "policy", difficulty="hard", group="version_chain")},
    {"id": "RD-024", "kb_id": KB_ID, "module": "rag",
     "question": "2024 年 6 月出差时，一线城市住宿标准是多少？",
     "annotation": A("policy_travel_v1", ["policy_travel_v1"], ["500元/晚"], "multi_condition",
                     vreq=VREQ_AS_OF_202406),
     "metadata": M("md", "policy", difficulty="hard", group="version_chain")},
    {"id": "RD-025", "kb_id": KB_ID, "module": "rag",
     "question": "按现行差旅制度提交报销单，除发票外还必须注明什么信息？",
     "annotation": A("policy_travel_v3", ["policy_travel_v3"],
                     ["出差事由与项目编号"], "multi_condition", vreq=VREQ_CURRENT),
     "metadata": M("pdf", "policy", difficulty="medium", group="version_chain")},

    # ---------------- 补充覆盖 ----------------
    {"id": "RD-032", "kb_id": KB_ID, "module": "rag",
     "question": "公司数据密级分为几级？数据对外导出需要谁的审批？",
     "annotation": A("policy_infosec", ["policy_infosec"],
                     ["public/internal/confidential三级", "数据保护官（DPO）书面审批"],
                     "multi_condition"),
     "metadata": M("md", "policy", difficulty="medium")},
    {"id": "RD-033", "kb_id": KB_ID, "module": "rag",
     "question": "2025 年全年客服工单总量和客户满意度分别是多少？",
     "annotation": A("report_cs_2025", ["report_cs_2025"],
                     ["58,320件", "92.4%"], "table_value"),
     "metadata": M("md", "report")},
    {"id": "RD-034", "kb_id": KB_ID, "module": "rag",
     "question": "星盾 2.0 是什么时候正式上线的？",
     "annotation": A("report_q2_product", ["report_q2_product"], ["2026-04-18"], "faq"),
     "metadata": M("docx", "report")},
    {"id": "RD-035", "kb_id": KB_ID, "module": "rag",
     "question": "门禁权限申请表里，王倩申请的是哪个区域？权限有效期多久？",
     "annotation": A("scan_access_request", ["scan_access_request"],
                     ["B2层机房", "90天（2026-07-01至2026-09-28）"], "exact_id"),
     "metadata": M("pdf", "form", difficulty="medium", group="scanned")},
    {"id": "RD-036", "kb_id": KB_ID, "module": "rag",
     "question": "报销单 BX-2026-0207 的报销金额是多少？",
     "annotation": A("table_expense_h1_2026", ["table_expense_h1_2026"],
                     ["5,210.50元"], "exact_id"),
     "metadata": M("csv", "table")},

    # ---------------- 拒答：无证据 ----------------
    {"id": "RD-026", "kb_id": KB_ID, "module": "rag",
     "question": "公司年假天数是多少？",
     "annotation": A(None, [], [], "no_evidence", refuse=True, reason="no_evidence"),
     "metadata": M("n/a", "n/a", no_answer=True)},
    {"id": "RD-027", "kb_id": KB_ID, "module": "rag",
     "question": "公司 2024 年营业收入是多少？",
     "annotation": A(None, [], [], "no_evidence", refuse=True, reason="no_evidence"),
     "metadata": M("n/a", "n/a", no_answer=True)},
    {"id": "RD-028", "kb_id": KB_ID, "module": "rag",
     "question": "星辰会议助手 v1.2 具体在哪一天上线？",
     "annotation": A("notes_product_review", ["notes_product_review"], [],
                     "no_evidence", refuse=True, reason="no_evidence"),
     "metadata": M("txt", "notes", difficulty="hard", no_answer=True)},
    {"id": "RD-029", "kb_id": KB_ID, "module": "rag",
     "question": "差旅餐费补贴标准是每餐多少元？",
     "annotation": A(None, [], [], "no_evidence", refuse=True, reason="no_evidence"),
     "metadata": M("n/a", "n/a", no_answer=True)},
    {"id": "RD-030", "kb_id": KB_ID, "module": "rag",
     "question": "公司的 CTO 是谁？",
     "annotation": A("faq_ops", ["faq_ops"], [], "no_evidence", refuse=True, reason="no_evidence"),
     "metadata": M("txt", "faq", difficulty="hard", no_answer=True)},

    # ---------------- 拒答：权限不足 ----------------
    {"id": "RD-031", "kb_id": KB_ID, "module": "rag",
     "question": "2026 年上半年公司经营性现金流是多少？",
     "annotation": A("report_fin_h1_2026", ["report_fin_h1_2026"], [],
                     "no_evidence", refuse=True, reason="permission",
                     perms=("finance_restricted",)),
     "metadata": M("pdf", "report", difficulty="medium", no_answer=True)},
]

# ---------------------------------------------------------------- 文档清单

def doc(doc_id, rel, fmt, doctype, text, perms="general", dept=None,
        version=None, scanned=False, group=None):
    return {"doc_id": doc_id, "file": rel, "format": fmt, "doc_type": doctype,
            "kb_id": KB_ID, "permission_scope": perms, "department": dept,
            "version": version, "is_scanned": scanned, "group": group,
            "key_facts": [], "section_anchors": []}

TRAVEL_VERSIONS = {
    "policy_travel_v1": {"version_id": "v1", "effective_from": "2024-04-01",
                          "effective_to": "2025-06-30", "supersedes": None,
                          "superseded_by": "policy_travel_v2"},
    "policy_travel_v2": {"version_id": "v2", "effective_from": "2025-07-01",
                          "effective_to": "2026-08-31", "supersedes": "policy_travel_v1",
                          "superseded_by": "policy_travel_v3"},
    "policy_travel_v3": {"version_id": "v3", "effective_from": "2026-09-01",
                          "effective_to": None, "supersedes": "policy_travel_v2",
                          "superseded_by": None},
}

DOCS = [
    doc("policy_attendance_rnd", "md/policy_考勤管理制度_研发中心.md", "md", "policy",
        MD_ATTENDANCE_RND, dept="HR"),
    doc("policy_attendance_mkt", "md/policy_考勤管理制度_市场部.md", "md", "policy",
        MD_ATTENDANCE_MKT, dept="HR", group="similar_pair"),
    doc("policy_seal", "md/policy_印章使用管理办法.md", "md", "policy", MD_SEAL, dept="行政"),
    doc("faq_employee", "md/faq_员工常见问题.md", "md", "faq", MD_FAQ_EMPLOYEE, dept="行政"),
    doc("manual_onboarding", "md/manual_新员工入职手册.md", "md", "manual",
        MD_ONBOARDING, dept="HR"),
    doc("policy_infosec", "md/policy_信息安全管理制度.md", "md", "policy", MD_INFOSEC,
        dept="数据安全委员会"),
    doc("contract_bluewhale", "md/contract_蓝鲸数据服务合同.md", "md", "contract",
        MD_CONTRACT_BLUEWHALE, dept="法务"),
    doc("report_cs_2025", "md/report_客服运营年度报告_2025.md", "md", "report",
        MD_REPORT_CS, dept="客服部"),
    doc("policy_travel_v1", "md/policy_差旅费管理办法_v1.md", "md", "policy",
        MD_TRAVEL_V1, dept="HR", version=TRAVEL_VERSIONS["policy_travel_v1"],
        group="version_chain"),

    doc("policy_travel_v2", "docx/policy_差旅费管理办法_v2.docx", "docx", "policy",
        None, dept="HR", version=TRAVEL_VERSIONS["policy_travel_v2"], group="version_chain"),
    doc("report_q2_product", "docx/report_Q2产品迭代总结_2026.docx", "docx", "report",
        None, dept="产品部"),
    doc("manual_meetingroom", "docx/manual_会议室使用规范.docx", "docx", "manual",
        None, dept="行政"),
    doc("policy_supplier", "docx/policy_供应商准入管理办法.docx", "docx", "policy",
        None, dept="采购部"),

    doc("policy_travel_v3", "pdf/policy_差旅费管理办法_v3.pdf", "pdf", "policy",
        None, dept="HR", version=TRAVEL_VERSIONS["policy_travel_v3"], group="version_chain"),
    doc("contract_office_lease", "pdf/contract_办公室租赁合同.pdf", "pdf", "contract",
        None, dept="行政", perms="finance_restricted"),
    doc("report_fin_h1_2026", "pdf/report_2026上半年财务摘要.pdf", "pdf", "report",
        None, dept="财务部", perms="finance_restricted"),

    doc("scan_asset_disposal", "pdf/scan_设备报废申请单.pdf", "pdf", "form",
        None, dept="行政", scanned=True, group="scanned"),
    doc("scan_access_request", "pdf/scan_门禁权限申请表.pdf", "pdf", "form",
        None, dept="IT", scanned=True, perms="it_admin", group="scanned"),

    doc("faq_ops", "txt/faq_运维值班FAQ.txt", "txt", "faq", TXT_FAQ_OPS, dept="IT"),
    doc("notes_product_review", "txt/notes_产品评审会议纪要_20260830.txt", "txt", "notes",
        TXT_NOTES_REVIEW, dept="产品部"),
    doc("dict_customer", "txt/readme_数据字典_客户域.txt", "txt", "reference",
        TXT_DICT_CUSTOMER, dept="数据部"),

    doc("table_hrcost_2026q2", "xlsx/table_2026Q2部门人力成本.xlsx", "xlsx", "table",
        None, dept="HR", perms="hr_confidential"),
    doc("table_pricing", "xlsx/table_产品定价表.xlsx", "xlsx", "table", None, dept="产品部"),
    doc("table_servers", "xlsx/table_服务器资产清单.xlsx", "xlsx", "table",
        None, dept="IT", perms="it_admin"),

    doc("table_sales_2026", "csv/table_区域销售月报_2026.csv", "csv", "table",
        None, dept="销售部"),
    doc("table_expense_h1_2026", "csv/table_差旅报销明细_2026H1.csv", "csv", "table",
        None, dept="财务部"),
    doc("table_tickets_2026", "csv/table_客服工单统计_2026.csv", "csv", "table",
        None, dept="客服部"),
]

# ---------------------------------------------------------------- 生成器实现

def write_text(rel: str, content: str, encoding: str = "utf-8") -> None:
    p = FILES_DIR / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding=encoding, newline="\n")


def gen_md_txt() -> None:
    for d in DOCS:
        if d["format"] == "md" and d["doc_id"] != "policy_travel_v1":
            pass
    write_text("md/policy_考勤管理制度_研发中心.md", MD_ATTENDANCE_RND)
    write_text("md/policy_考勤管理制度_市场部.md", MD_ATTENDANCE_MKT)
    write_text("md/policy_印章使用管理办法.md", MD_SEAL)
    write_text("md/faq_员工常见问题.md", MD_FAQ_EMPLOYEE)
    write_text("md/manual_新员工入职手册.md", MD_ONBOARDING)
    write_text("md/policy_信息安全管理制度.md", MD_INFOSEC)
    write_text("md/contract_蓝鲸数据服务合同.md", MD_CONTRACT_BLUEWHALE)
    write_text("md/report_客服运营年度报告_2025.md", MD_REPORT_CS)
    write_text("md/policy_差旅费管理办法_v1.md", MD_TRAVEL_V1)
    write_text("txt/faq_运维值班FAQ.txt", TXT_FAQ_OPS)
    write_text("txt/notes_产品评审会议纪要_20260830.txt", TXT_NOTES_REVIEW)
    write_text("txt/readme_数据字典_客户域.txt", TXT_DICT_CUSTOMER)


def gen_docx() -> None:
    from docx import Document

    def save(rel, build):
        p = FILES_DIR / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        d = Document()
        build(d)
        d.save(str(p))

    def travel_v2(d: Document) -> None:
        d.add_heading("差旅费管理办法（V2）", level=1)
        d.add_paragraph("制度编号：XC-HR-2025-047")
        d.add_paragraph("发布日期：2025-06-10    生效日期：2025-07-01")
        d.add_paragraph("本版本自 2025-07-01 起施行，同时废止 V1 版（XC-HR-2024-032）。")
        d.add_heading("1. 住宿标准", level=2)
        d.add_paragraph("一线城市（北京/上海/广州/深圳）：600 元/晚。")
        d.add_paragraph("二线城市：450 元/晚。")
        d.add_heading("2. 发票要求", level=2)
        d.add_paragraph("住宿、交通费用须提供增值税电子发票，纸质发票不再受理。")
        d.add_heading("3. 行程变更", level=2)
        d.add_paragraph("行程变更须在 24 小时内提交补充说明。")

    def q2_product(d: Document) -> None:
        d.add_heading("Q2 产品迭代总结（2026）", level=1)
        d.add_paragraph("星盾 2.0 于 2026-04-18 正式上线，灰度期 3 周，覆盖 40% 客户。")
        d.add_paragraph("本季度累计修复 P1 级缺陷 12 个，P2 级缺陷 37 个。")
        d.add_paragraph("下一季度重点：会议助手语音转写能力。")

    def meetingroom(d: Document) -> None:
        d.add_heading("会议室使用规范", level=1)
        d.add_heading("1. 预订", level=2)
        d.add_paragraph("会议室通过「星辰办公」App 预订，单次最长 3 小时。")
        d.add_paragraph("如需取消，请至少提前 30 分钟释放，避免资源浪费。")
        d.add_heading("2. 设备", level=2)
        d.add_paragraph("投影仪 HDMI 转接头在 B1 层前台借阅，用后当日归还。")
        d.add_heading("3. 卫生", level=2)
        d.add_paragraph("会后请自行清理桌面，白板内容保留不得超过 24 小时。")

    def supplier(d: Document) -> None:
        d.add_heading("供应商准入管理办法", level=1)
        d.add_paragraph("供应商准入实行百分制评分，综合评分达到 80 分及以上方可准入。")
        d.add_paragraph("供应商年审每年 3 月统一开展，年审不合格的暂停合作资格。")
        d.add_paragraph("列入黑名单的供应商，3 年内不得再次申请准入。")
        table = d.add_table(rows=4, cols=3)
        table.style = "Table Grid"
        for i, row in enumerate([("维度", "权重", "说明"),
                                 ("资质合规", "30%", "营业执照、行业资质"),
                                 ("交付能力", "40%", "产能、历史履约记录"),
                                 ("价格竞争力", "30%", "报价与成本合理性")]):
            for j, val in enumerate(row):
                table.rows[i].cells[j].text = val

    save("docx/policy_差旅费管理办法_v2.docx", travel_v2)
    save("docx/report_Q2产品迭代总结_2026.docx", q2_product)
    save("docx/manual_会议室使用规范.docx", meetingroom)
    save("docx/policy_供应商准入管理办法.docx", supplier)


def _wrap(text: str, width: int) -> list[str]:
    return [text[i:i + width] for i in range(0, len(text), width)]


def _text_pdf(path: Path, title: str, lines: list[str]) -> None:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas

    pdfmetrics.registerFont(TTFont("SimHei", FONT_HEI))
    path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setTitle(title)
    w, h = A4
    margin, y, line_h = 56, h - 72, 22
    for raw in lines:
        for ln in _wrap(raw, 46):
            if y < margin:
                c.showPage()
                y = h - 72
            c.setFont("SimHei", 11)
            c.drawString(margin, y, ln)
            y -= line_h
        if raw and (raw.startswith("#") or raw.startswith("##")):
            y -= 6
    c.save()


def gen_pdf_text() -> None:
    _text_pdf(FILES_DIR / "pdf/policy_差旅费管理办法_v3.pdf", "差旅费管理办法 V3", [
        "差旅费管理办法（V3）",
        "制度编号：XC-HR-2026-005    发布日期：2026-08-05",
        "生效日期：2026-09-01，同时废止 V2 版（XC-HR-2025-047）。",
        "",
        "一、住宿标准",
        "一线城市（北京/上海/广州/深圳）：700 元/晚。",
        "二线城市：520 元/晚。",
        "",
        "二、报销要求",
        "报销单须注明出差事由与项目编号，缺项不予受理。",
        "住宿、交通费用须提供增值税电子发票，电子发票重复报销将按公司",
        "红线规定追责。",
        "",
        "三、交通",
        "高铁二等座、经济舱凭票实报实销；市内交通凭行程说明实报。",
    ])
    _text_pdf(FILES_DIR / "pdf/contract_办公室租赁合同.pdf", "办公室租赁合同", [
        "办公室租赁合同",
        "出租方（甲方）：恒基地产发展有限公司",
        "承租方（乙方）：星辰云科技有限公司",
        "",
        "一、租赁标的：闽江大道 88 号星辰大厦 12~15 层。",
        "二、租期：2025 年 1 月 1 日至 2027 年 12 月 31 日。",
        "三、租金：月租金人民币 185,000 元（18.5 万元），含物业费。",
        "四、支付方式：押三付一，押金为 3 个月租金。",
        "五、违约：任一方提前解约须提前 90 日书面通知并支付 2 个月租金违约金。",
    ])
    _text_pdf(FILES_DIR / "pdf/report_2026上半年财务摘要.pdf", "2026上半年财务摘要", [
        "2026 年上半年财务摘要（机密）",
        "密级：confidential    发布：财务部    日期：2026-07-15",
        "",
        "一、营业收入：上半年营业收入人民币 234,000,000 元（2.34 亿元），",
        "    同比增长 18.6%。",
        "二、毛利率：61.2%。",
        "三、经营性现金流：人民币 3,180 万元。",
        "四、费用：研发费用 6,820 万元，销售费用 4,310 万元。",
    ])


def _scan_pdf(path: Path, lines: list[str]) -> None:
    from PIL import Image, ImageDraw, ImageFont

    W, H = 1654, 2339  # A4 @200dpi
    img = Image.new("RGB", (W, H), (250, 250, 247))
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(FONT_HEI, 54)
    font_small = ImageFont.truetype(FONT_HEI, 44)
    y = 180
    for ln in lines:
        f = font if ln.startswith("【") or ln.startswith("星辰") else font_small
        draw.text((140, y), ln, fill=(28, 28, 30), font=f)
        y += 96
    draw.rectangle([100, 100, W - 100, H - 100], outline=(180, 180, 175), width=3)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(path), "PDF", resolution=200.0)


def gen_pdf_scan() -> None:
    _scan_pdf(FILES_DIR / "pdf/scan_设备报废申请单.pdf", [
        "星辰云科技有限公司  设备报废申请单",
        "",
        "申请日期：2026-05-28",
        "申请人：陈斌（研发中心）",
        "资产编号：ZC-2021-118",
        "设备名称：ThinkPad T15 笔记本电脑",
        "购入日期：2021-08-15",
        "报废理由：主板损坏，维修报价 2,900 元，超过设备残值。",
        "",
        "审批记录：",
        "  部门负责人：同意（2026-06-02）",
        "  行政部：同意（2026-06-05）",
        "  财务部：同意（2026-06-10）",
        "  总经理：批准（2026-06-12）——审批完成日期 2026-06-12",
    ])
    _scan_pdf(FILES_DIR / "pdf/scan_门禁权限申请表.pdf", [
        "星辰云科技有限公司  门禁权限申请表",
        "",
        "申请人：王倩    工号：XC-0847（运维部）",
        "申请区域：B2 层机房",
        "权限有效期：90 天（2026-07-01 至 2026-09-28）",
        "申请事由：季度硬件巡检",
        "",
        "审批记录：",
        "  部门负责人：同意（2026-06-24）",
        "  安全负责人：李洪，同意（2026-06-26）",
    ])


def gen_xlsx() -> None:
    import openpyxl

    def save(rel, rows, sheet="Sheet1"):
        p = FILES_DIR / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = sheet
        for row in rows:
            ws.append(row)
        for cell in ws[1]:
            cell.font = openpyxl.styles.Font(bold=True)
        wb.save(str(p))

    save("xlsx/table_2026Q2部门人力成本.xlsx", [
        ("部门", "2026Q2人力成本（万元）", "同比"),
        ("研发部", 486.5, "+12.4%"),
        ("市场部", 152.3, "-3.1%"),
        ("客服部", 98.7, "+5.6%"),
        ("行政部", 61.2, "+0.8%"),
        ("财务部", 55.4, "-1.2%"),
    ], sheet="Q2人力成本")

    save("xlsx/table_产品定价表.xlsx", [
        ("版本", "年费（元/年）", "包含席位"),
        ("基础版", 999, 5),
        ("专业版", 3999, 20),
        ("企业版", 12999, "不限"),
    ], sheet="定价")

    save("xlsx/table_服务器资产清单.xlsx", [
        ("资产编号", "型号", "机房", "上架日期"),
        ("SL-0012", "华为 2288H V5", "B2机房", "2024-05-11"),
        ("SL-0031", "浪潮 NF5280M6", "A1机房", "2024-11-03"),
        ("SL-0058", "戴尔 R750", "B2机房", "2025-03-19"),
    ], sheet="资产清单")


def gen_csv() -> None:
    def save(rel, header, rows):
        p = FILES_DIR / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(header)
            w.writerows(rows)

    save("csv/table_区域销售月报_2026.csv",
         ["月份", "区域", "销售额（万元）", "完成率"],
         [("2026-04", "华东", 103.2, "88.1%"), ("2026-04", "华南", 96.8, "91.4%"),
          ("2026-05", "华东", 141.2, "96.5%"), ("2026-05", "华南", 118.5, "93.2%"),
          ("2026-06", "华东", 168.4, "102.7%"), ("2026-06", "华南", 122.7, "95.9%")])

    save("csv/table_差旅报销明细_2026H1.csv",
         ["报销单号", "申请人", "部门", "金额（元）", "月份"],
         [("BX-2026-0132", "王倩", "市场部", 3860.00, "2026-03"),
          ("BX-2026-0207", "陈斌", "研发中心", 5210.50, "2026-04"),
          ("BX-2026-0356", "苏黎", "产品部", 2478.00, "2026-05"),
          ("BX-2026-0411", "李洪", "客服部", 1905.00, "2026-06")])

    save("csv/table_客服工单统计_2026.csv",
         ["月份", "工单量", "解决率", "平均响应（秒）"],
         [("2026-01", 4210, "91.2%", 52), ("2026-02", 3864, "90.8%", 49),
          ("2026-03", 4502, "92.1%", 47), ("2026-04", 4688, "93.0%", 46),
          ("2026-05", 4933, "92.6%", 44), ("2026-06", 5120, "93.8%", 41)])


# ---------------------------------------------------------------- manifest 落盘

MANIFEST_ANNOTATIONS = {
    "policy_attendance_rnd": (["弹性打卡09:30至10:00", "月内迟到3次以内且每次≤10分钟不计罚",
                               "加班满2小时起算", "调休当年12月31日前用完"],
                              ["弹性区间为 09:30 至 10:00", "满 2 小时起算"]),
    "policy_attendance_mkt": (["弹性打卡08:30至09:00", "月内迟到5次以内且每次≤15分钟不计罚",
                               "加班满1.5小时起算"],
                              ["弹性区间为 08:30 至 09:00", "满 1.5 小时起算"]),
    "policy_seal": (["公章由行政部双人分管", "合同用印须法务会签", "用印登记保存5年"],
                    ["制度编号：XC-XZ-2023-009", "行政部"]),
    "faq_employee": (["访客Wi-Fi: XC-Guest, 密码每月1日更新", "报销审核10个工作日内",
                      "工牌补办3个工作日/20元", "班车3条线路", "体检9~10月"],
                     ["访客 Wi-Fi 名称 XC-Guest", "10 个工作日内"]),
    "manual_onboarding": (["试用期6个月", "导师辅导3个月", "转正答辩提前5个工作日预约"],
                          ["需提前 5 个工作日 在 OA 上预约答辩时间"]),
    "policy_infosec": (["密级public/internal/confidential", "导出须DPO书面审批", "离岗当日回收权限"],
                       ["public（公开）、internal（内部）、confidential（机密）"]),
    "contract_bluewhale": (["合同编号XC-HT-2025-041", "合同金额120万元",
                            "服务期2025-03-01至2027-02-28", "逾期违约金月服务费5%"],
                           ["合同编号：XC-HT-2025-041", "120 万元"]),
    "report_cs_2025": (["2025年工单总量58,320件", "满意度92.4%", "平均首次响应45秒"],
                       ["58,320 件", "92.4%"]),
    "policy_travel_v1": (["一线城市住宿500元/晚", "二线城市380元/晚", "纸质增值税发票"],
                         ["500 元/晚"]),
    "policy_travel_v2": (["一线城市住宿600元/晚", "二线城市450元/晚", "须增值税电子发票",
                          "行程变更24小时内补充说明", "废止V1"],
                         ["600 元/晚", "废止 V1"]),
    "policy_travel_v3": (["一线城市住宿700元/晚", "二线城市520元/晚",
                          "报销单须注明出差事由与项目编号", "废止V2"],
                         ["700 元/晚", "出差事由与项目编号"]),
    "report_q2_product": (["星盾2.0于2026-04-18上线", "灰度3周覆盖40%客户", "修复P1缺陷12个"],
                          ["2026-04-18"]),
    "manual_meetingroom": (["星辰办公App预订", "单次最长3小时", "取消提前30分钟",
                            "HDMI转接头B1层前台借阅"],
                           ["单次最长 3 小时", "B1 层前台"]),
    "policy_supplier": (["准入综合评分≥80分", "年审每年3月", "黑名单3年内不得再准入"],
                        ["80 分及以上", "每年 3 月"]),
    "contract_office_lease": (["租期2025-01-01至2027-12-31", "月租金18.5万元",
                               "押三付一（押金3个月租金）"],
                              ["185,000 元（18.5 万元）", "押三付一"]),
    "report_fin_h1_2026": (["上半年营收2.34亿元", "毛利率61.2%", "经营性现金流3,180万元"],
                           ["61.2%", "3,180 万元"]),
    "scan_asset_disposal": (["资产编号ZC-2021-118（ThinkPad T15）", "申请人陈斌",
                             "审批完成日期2026-06-12", "报废理由：维修费超残值"],
                            ["ZC-2021-118", "2026-06-12"]),
    "scan_access_request": (["申请人王倩（工号XC-0847）", "申请区域B2层机房",
                             "有效期90天（2026-07-01至2026-09-28）", "审批人李洪"],
                            ["B2 层机房", "90 天"]),
    "faq_ops": (["值班电话0591-8888-6677", "P1级15分钟内响应", "升级路径值班工程师→值班经理→CTO"],
                ["0591-8888-6677", "15 分钟内响应"]),
    "notes_product_review": (["会议助手v1.2排期2026年10月上线（具体日期待9月评审）",
                              "Q4预算追加35万元", "负责人苏黎"],
                             ["2026 年 10 月上线", "35 万元"]),
    "dict_customer": (["c_user_id格式CU+8位数字", "c_tier枚举A/B/C",
                       "c_created_at为UTC+8 ISO8601"],
                      ["CU + 8 位数字"]),
    "table_hrcost_2026q2": (["2026Q2研发部人力成本486.5万元", "市场部152.3万元",
                             "客服部98.7万元"],
                            ["研发部", "486.5"]),
    "table_pricing": (["基础版999元/年含5席位", "专业版3,999元/年含20席位",
                       "企业版12,999元/年席位不限"],
                      ["12999", "不限"]),
    "table_servers": (["SL-0012华为2288H在B2机房", "SL-0031浪潮NF5280M6在A1机房",
                       "SL-0058戴尔R750在B2机房"],
                      ["SL-0031", "A1机房"]),
    "table_sales_2026": (["2026-06华东销售额168.4万元", "2026-06华南122.7万元",
                          "2026-05华东141.2万元"],
                         ["168.4", "华东"]),
    "table_expense_h1_2026": (["BX-2026-0132王倩3,860元", "BX-2026-0207陈斌5,210.5元",
                               "BX-2026-0356苏黎2,478元", "BX-2026-0411李洪1,905元"],
                              ["BX-2026-0207", "5,210.50"]),
    "table_tickets_2026": (["2026-06工单量5,120件", "2026-06解决率93.8%",
                            "2026-06平均响应41秒"],
                           ["5,120", "93.8%"]),
}


def build_manifest() -> dict:
    for d in DOCS:
        kf, anchors = MANIFEST_ANNOTATIONS[d["doc_id"]]
        d["key_facts"] = kf
        d["section_anchors"] = anchors
    return {
        "version": "0.1.0-probe",
        "name": "rag_100_docs",
        "kb_id": KB_ID,
        "generated_at": "2026-09-17",
        "note": ("R3 探路版语料清单（27 份）。每份文档的关键事实在生成期固化在源内容中；"
                 "is_scanned=true 的 PDF 为文字渲染成图像封装（无真实 OCR，"
                 "在线 OCR 回填结果应与 key_facts 一致）。expected_chunk_ids 需索引后回填。"),
        "documents": DOCS,
        "groups": {
            "similar_pair": ["policy_attendance_rnd", "policy_attendance_mkt"],
            "version_chain": ["policy_travel_v1", "policy_travel_v2", "policy_travel_v3"],
            "scanned": ["scan_asset_disposal", "scan_access_request"],
        },
        "format_counts": {},
    }


# ---------------------------------------------------------------- 校验

QUERY_TYPES = {"faq", "exact_id", "multi_condition", "table_value",
               "cross_doc", "low_confidence", "no_evidence"}
REQUIRED_FIELDS = ["doc_id", "expected_doc_ids", "expected_chunk_ids", "key_facts",
                   "query_type", "should_refuse", "version_requirement", "permission_scope"]


def validate(dataset: dict, manifest: dict) -> list[str]:
    errs: list[str] = []
    doc_ids = {d["doc_id"] for d in manifest["documents"]}

    fc: dict[str, int] = {}
    for d in manifest["documents"]:
        fc[d["format"]] = fc.get(d["format"], 0) + 1
    manifest["format_counts"] = fc
    for fmt in ("pdf", "docx", "md", "txt", "xlsx", "csv"):
        if fc.get(fmt, 0) == 0:
            errs.append(f"缺少格式: {fmt}")

    g = manifest["groups"]
    if len(g["similar_pair"]) != 2:
        errs.append("相似文档组必须恰好 2 份")
    if len(g["version_chain"]) != 3:
        errs.append("版本链必须 3 份")
    if len(g["scanned"]) < 1:
        errs.append("扫描件至少 1 份")
    for rel in g["similar_pair"] + g["version_chain"] + g["scanned"]:
        if rel not in doc_ids:
            errs.append(f"group 引用不存在的 doc_id: {rel}")

    covered: set[str] = set()
    for c in dataset["test_cases"]:
        cid, ann = c["id"], c.get("annotation", {})
        missing = [f for f in REQUIRED_FIELDS if f not in ann]
        if missing:
            errs.append(f"{cid}: 缺标注字段 {missing}")
            continue
        if ann["query_type"] not in QUERY_TYPES:
            errs.append(f"{cid}: 非法 query_type {ann['query_type']}")
        if ann["should_refuse"] != (ann["refusal_reason"] is not None):
            errs.append(f"{cid}: should_refuse 与 refusal_reason 不一致")
        refs = set(ann["expected_doc_ids"])
        if ann["doc_id"]:
            refs.add(ann["doc_id"])
        for r in refs:
            if r not in doc_ids:
                errs.append(f"{cid}: 引用不存在的 doc_id {r}")
        if ann["should_refuse"] and ann["refusal_reason"] == "no_evidence" \
                and ann["doc_id"] is not None and ann["key_facts"]:
            errs.append(f"{cid}: 拒答用例不应带 key_facts")
        covered |= refs

    uncovered = doc_ids - covered
    if uncovered:
        errs.append(f"未被任何用例覆盖的文档: {sorted(uncovered)}")
    return errs


# ---------------------------------------------------------------- main

def main() -> int:
    print("== R3 探路版生成器 ==")
    gen_md_txt();     print("[1/6] MD/TXT 12 份  完成")
    gen_docx();       print("[2/6] DOCX 4 份    完成")
    gen_pdf_text();   print("[3/6] 文本 PDF 3 份 完成")
    gen_pdf_scan();   print("[4/6] 扫描件 PDF 2 份（文字渲染成图）完成")
    gen_xlsx();       print("[5/6] XLSX 3 份    完成")
    gen_csv();        print("[6/6] CSV 3 份     完成")

    manifest = build_manifest()
    (HERE / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"manifest.json 写入完成（{len(manifest['documents'])} 份文档）")

    dataset = {
        "version": "0.1.0-probe",
        "name": "rag_100_docs",
        "kb_id": KB_ID,
        "_comment": (
            "R3 探路版测试集（任务书 §4）。8 个标注字段位于每条用例的 annotation 内："
            "doc_id / expected_doc_ids / expected_chunk_ids / key_facts(关键事实) / "
            "query_type(查询类型, 7 类) / should_refuse(是否拒答, 辅以 refusal_reason) / "
            "version_requirement(版本要求) / permission_scope(权限范围)。"
            "expected_chunk_ids 在索引完成前不可知，暂为 null，以 expected_chunk_anchors "
            "(原文锚点) 替代，索引后回填。无答案用例 key_facts 为空且应拒答。"
            "权限用例的 should_refuse 以「general 权限视角」标注。"),
        "fixture_dir": "../fixtures/rag_100_docs/files",
        "documents_count": len(manifest["documents"]),
        "test_cases": CASES,
    }
    errs = validate(dataset, manifest)
    if errs:
        print("校验失败：")
        for e in errs:
            print("  -", e)
        return 1
    DATASET_PATH.write_text(
        json.dumps(dataset, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"rag_100_docs.json 校验通过并写入（{len(CASES)} 条用例，"
          f"{len(manifest['documents'])} 份文档，格式覆盖 {manifest['format_counts']}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
