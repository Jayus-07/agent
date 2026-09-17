# -*- coding: utf-8 -*-
"""R3 扩容版测试集生成器（rag_100_docs）——27 份探路版 → 100 份全量版。

用途：
  生成 100 份多格式语料 fixtures（PDF 文本版 / PDF 复杂版面 / 扫描件图版 / DOCX /
  MD / TXT / XLSX 单 Sheet 与多 Sheet / CSV 含 UTF-8-sig、UTF-8 无 BOM、GBK、GB18030
  四种中文编码）+ backend/evaluation/datasets/rag_100_docs.json
  （任务书 §4 8 个标注字段：doc_id / expected_doc_ids / expected_chunk_ids /
  关键事实 / 查询类型 / 是否拒答 / 版本要求 / 权限范围），并做一致性自校验。

复现：
  ./.venv/Scripts/python.exe backend/evaluation/fixtures/rag_100_docs/generate_fixtures.py

边界：
  仅生成内容与 JSON，不修改 backend 其他代码；expected_chunk_ids 在索引完成前不可知，
  以 null + expected_chunk_anchors（原文锚点）表示，索引后回填。

ⓘ 2026-09-17 R3 第五阶段之后的扩容要点：
  - 探路版 27 份与 36 条用例（RD-001~RD-036）**逐字保留**，包括已修正的两处权限语义
    （RD-031 perms=general 拒答、RD-035 perms 含 it_admin）与 30 条已回填的
    expected_chunk_ids（见 BACKFILLED_CHUNK_IDS，生成后回填回来，不得被重建清零）。
  - 补齐 §4 覆盖清单里还缺的形态：复杂版面 PDF（双栏 + 表格 + 页眉页脚 + 多页长文）、
    多 Sheet XLSX、中文编码 CSV（GBK/GB18030/UTF-8 无 BOM）、
    更多业务类型（legal 法务 / sop 标准作业流程 / spec 技术规范）、
    更多版本链（应急响应 SOP v1→v2→v3、费用报销 v1→v2→v3、信息安全 v1→v2）
    与跨文档关联组（采购、故障、招聘、财务、产品、数据合规）。
  - 生成改为「数据驱动」：DOCS 清单是全源点，任何一份文档缺失生成器实现都会在
    自校验阶段报 MISSING_GENERATOR（避免清单与文件失配）。
  - 不得引入与既有拒答（无证据）用例相冲突的事实：
    RD-026 年假天数 / RD-027 2024 年营业收入 / RD-029 差旅餐费补贴 /
    RD-030 CTO 姓名 / RD-028 会议助手 v1.2 上线日期——新语料一律不涉及。
    （2026 年报初稿曾写「2024 年营业收入 33,960 万元 / 同比 +21.3%」，会让 RD-027
      由拒答变成可答，已删除同比列，仅保留 2025 年度口径。）
  - 落地结果：100 份文档（md 27 / docx 15 / pdf 20 / txt 10 / xlsx 13 / csv 15）、
    169 条用例（RD-001~RD-169），7 类 query_type 全覆盖；版本链 4 条、相似对 2 组、
    跨文档关联组 6 组、复杂版面 PDF 7 份、多页长文 5 份、扫描件 4 份。
  - 已知联动缺陷（本生成器做的是语料侧规避，不是根治）：
    CsvParser 的多级表头启发式「前 3 行数值占比 < 20% 即判为表头」会把全字符串表的
    前几行吞成拍平表头，因此新增的全文本表都刻意保留了数值列。

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

# ---------------------------------------------------------------- 新增 MD / TXT 源文本
# （2026-09-17 R3 扩容：Legal / SOP / 复杂政策 / 长文手册 / 技术规范 / 会议纪要）

MD_LEGAL_NNN = """# 双向保密协议（NNN）

协议编号：XC-NDA-2026-007
缔约双方：{c} 与 深蓝智能科技（深圳）有限公司
生效日期：2026-02-09

## 1. 保密信息范围
包含技术资料、源代码、算法参数、客户名单、定价策略以及双方在谈判过程中
披露的一切非公开信息。

## 2. 保密期限
自签署之日起 **5 年**，即使协议终止或合作结束，保密义务仍然存续。

## 3. 不使用义务
接收方不得将保密信息用于本次合作之外的任何目的，亦不得反向工程。

## 4. 例外情形
以下情形不属于泄密：接收时已公开的信息、接收方独立开发所得的信息、
依法必须披露且已提前 3 个工作日书面通知对方的信息。

## 5. 违约责任
违反本协议的，违约方支付违约金 **50 万元**；造成实际损失超过违约金的，
按实际损失赔偿。
""".format(c=COMPANY)

MD_LEGAL_IP_ASSIGNMENT = """# 知识产权转让协议

协议编号：XC-IP-2025-019
受让方：{c}
转让方：麦谷人工智能研究院

## 1. 转让标的
含 **3 项发明专利**（ZL2023-1-0187xx 系列）与 **2 项软件著作权**
（智能对话引擎 V2、知识抽取工具 V1）。

## 2. 转让对价
转让总价为人民币 **80 万元**，分两期支付：协议签署后 15 个工作日内支付 50%，
权属变更登记完成后支付剩余 50%。

## 3. 权属变更
双方于 **2025-11-20** 完成全部权属变更登记，登记费用各自承担一半。

## 4. 后续改进
转让完成后基于上述技术形成的改进成果归受让方单独所有。
""".format(c=COMPANY)

MD_LEGAL_PURCHASE_ZHONGKE = """# 设备采购合同（星辰云 × 中科智联）

合同编号：XC-CG-2026-014
采购方：{c}
供应方：中科智联（武汉）信息技术有限公司

## 1. 采购标的
GPU 服务器 **12 台**，配置为双路 CPU、8 张加速卡、1TB 内存。

## 2. 合同总价
合同总价人民币 **468 万元**，含运输、安装调试与三年原厂质保。

## 3. 交货与验收
交货期为合同生效之日起 **45 天**；到货后 10 个工作日内完成开箱验收与压力测试。

## 4. 质保
质保期 **3 年**，质保期内故障 4 小时内响应、次工作日上门。

## 5. 违约责任
逾期交货的，每逾期一日按合同总价的 **0.5%** 支付违约金，违约金累计
**不超过合同总价的 5%**；逾期超过 30 日的采购方可解除合同。
""".format(c=COMPANY)

MD_LEGAL_SETTLEMENT = """# 争议和解备忘录

编号：XC-HJ-2026-003
签署日期：2026-07-18

## 1. 争议背景
双方就 2025 年度数据服务接口可用性指标达成与否存在理解分歧。

## 2. 和解方案
由 {c} 一次性支付 **35 万元** 了结全部争议，款项于备忘录签署后
20 个工作日内支付。

## 3. 互不追究
款项支付完毕后，双方就上述争议互不追究任何法律责任，并撤回已提起的仲裁申请。

## 4. 保密
和解金额与条款对外保密，任一方披露的按和解金额的 30% 支付违约金。
""".format(c=COMPANY)

MD_POLICY_EXPORT_CONTROL = """# 出口管制合规管理办法

制度编号：XC-FL-2025-022
发布部门：法务部
生效日期：2025-09-01

## 1. 适用范围
所有涉及境外交付、跨境技术支持与海外子公司的物项流转。

## 2. 受控物项清单
由法务部维护，**每季度**更新一次；业务部门在报价前须核对最新版本。

## 3. 审查节点
合同签署前须完成出口管制筛查，筛查记录随合同归档保存 10 年。

## 4. 违规责任
未履行筛查义务造成违规的，**直接责任人当年度绩效一票否决**，
并移交公司纪律委员会处理。
"""

MD_POLICY_RECRUITMENT = """# 招聘管理制度

制度编号：XC-HR-2025-011
发布部门：人力资源部
生效日期：2025-05-06

## 1. 编制管理
用人补充编制（HC）须由 **CEO 终审**，未经终审不得启动招聘。

## 2. 内推奖励
内部推荐人选录用并通过试用期的，推荐人获奖励 **8,000 元/人**，
在被推荐人转正的次月随工资发放。

## 3. 背景调查
拟录用人员在发 Offer 前完成背景调查，**核心岗位背调覆盖率 100%**。

## 4. 录用回避
近亲属在同一汇报线上任职的须回避，不得形成直接汇报关系。
"""

MD_POLICY_PERFORMANCE = """# 绩效考核管理办法

制度编号：XC-HR-2025-029
发布部门：人力资源部
生效日期：2025-10-01

## 1. 考核周期
全员**半年度**考核，分别在 1 月与 7 月开展。

## 2. 等级分布
考核结果分为 **A / B / C / D** 四档，实行强制分布：
A 档**不超过 20%**，D 档**不低于 5%**。

## 3. 结果应用
**连续两次考核为 D** 的员工进入绩效改进计划（PIP），改进期 **3 个月**；
改进期满仍未达标的，按人岗不匹配处理。

## 4. 申诉
对结果有异议的，可在结果告知后 10 个工作日内向 HRBP 提出书面申诉。
"""

MD_POLICY_SOFTWARE_ASSET = """# 软件资产管理办法

制度编号：XC-IT-2025-014
发布部门：IT 部
生效日期：2025-08-01

## 1. 正版化目标
生产环境软件的**正版化率目标为 100%**，不得存在未授权许可。

## 2. 禁用清单
以下软件禁止在生产环境使用：破解版软件、试用期已届满未转正的软件、
来源不明的第三方插件。

## 3. 盘点
IT 部于**每年 12 月**开展全公司软件盘点，盘点结果与次年预算挂钩。

## 4. 例外采购
确因业务需申请的，须经 IT 部评估并报分管副总裁批准。
"""

MD_POLICY_EXPENSE_V1 = """# 费用报销管理办法（V1）

制度编号：XC-CW-2024-021
发布日期：2024-06-20
生效日期：2024-07-01

## 1. 审批层级
报销单须经**部门负责人 → 财务部**两级审批。

## 2. 报销时限
费用发生后须在 **60 日内**提交报销，跨年度费用不得延至次年。

## 3. 凭证要求
以纸质增值税发票为主要凭证，遗失发票的可凭加盖公章的发票复印件报销。

## 4. 支付方式
报销款项每月 15 日与月末两次集中支付。
"""

MD_POLICY_OVERTIME_RND = """# 加班与工时管理制度（研发中心）

制度编号：XC-HR-2025-016
发布部门：人力资源部
生效日期：2025-04-01

## 1. 工时上限
研发中心员工**每月加班不得超过 36 小时**，超出部分须书面说明原因。

## 2. 加班费计发
工作日加班按本人小时工资的 **1.5 倍**计发；休息日安排加班的优先安排补休，
不能补休的按 2 倍计发。

## 3. 审批
加班须在当月最后一个工作日之前完成补录审批，未补录的不计入加班时长。

## 4. 夜班保障
20:00 之后仍在岗加班的，可报销当次返程交通费用。
"""

MD_POLICY_OVERTIME_CS = """# 加班与工时管理制度（客服部）

制度编号：XC-HR-2025-017
发布部门：人力资源部
生效日期：2025-04-01

## 1. 排班
客服部实行 **28 天为一周期的轮班制**，遇大促期间启用机动班。

## 2. 工时上限
客服岗位**每月加班不得超过 24 小时**，超出部分须报客服总监批准。

## 3. 法定节假日
法定节假日加班按 **3 倍**工资计发，或由员工自主选择补休。

## 4. 夜班保障
夜班（22:00 至次日 08:00）在岗的，每班次发放夜间津贴 60 元。
"""

MD_SOP_REFUND = """# 客户退款处理 SOP

编号：XC-SOP-CS-2025-008
适用：客服部退款专员
版本日期：2025-11-12

## 1. 受理
客户提出退款申请后 1 个工作日内完成资料初审并出具受理编号。

## 2. 审核
退款审核须在 **3 个工作日**内完成；**单笔金额超过 5 万元的须报财务总监审批**。

## 3. 到账
财务付款后，**到账周期为 7 至 15 个工作日**（视开户行而定）。

## 4. 例外
超过 30 日未到账的，由客服专员主动回访并推送《退款进度说明》。
"""

MD_SOP_VENDOR_ONBOARDING = """# 供应商准入作业流程 SOP

编号：XC-SOP-PU-2025-003
适用：采购部供应商管理员

## 1. 材料清单
准入须提交 **5 项材料**：营业执照、财务状况表、近三年履约记录、
质量体系认证、合规承诺函。

## 2. 保证金
通过准入的供应商须缴纳履约保证金 **10 万元**，合作终止且无违约的
30 日内无息退还。

## 3. 办理时限
材料齐全起 **15 个工作日内**完成准入审核并出具结论。

## 4. 复核
已准入供应商的资质每年度复核一次，复核不合格的暂停下单资格。
"""

MD_SOP_INCIDENT_V1 = """# 线上故障应急响应 SOP（V1）

编号：XC-SOP-IT-2024-006
发布日期：2024-09-20
生效日期：2024-10-01

## 1. 故障分级
P1 级为服务整体不可用，P2 级为核心功能受损，P3 级为一般体验问题。

## 2. 修复时限
P1 级故障须在 **4 小时内**恢复服务；P2 级 12 小时内；P3 级 3 个工作日内。

## 3. 通知渠道
故障通报经**邮件 + 短信**双通道下发，30 分钟内完成首次通报。

## 4. 复盘
P1 级故障须在恢复后 3 个工作日内提交故障复盘报告。
"""

MD_REPORT_INCIDENT_2026Q2 = """# 2026 年第二季度安全事件报告

编制部门：安全应急中心
报告日期：2026-07-08

## 1. 事件概况
本季度共处置安全事件 **14 起**，其中 P1 级 1 起、P2 级 3 起、P3 级 10 起。

## 2. 重大事件
2026-05-19 发生的网关鉴权绕行事件判定为 P1 级，
**受影响账户 1,243 个**，全部账户已于次日完成强制密码重置。

## 3. 平均时效
平均检测时长 **23 分钟**，平均止血时长 1 小时 47 分。

## 4. 改进项
三季度重点：接入行为基线自学习、缩短特权操作的二次审批链路。
"""

MD_MANUAL_OPS_RUNBOOK = """# 运维作业手册（生产环境）

版本：v2026.08    编制部门：IT 运维部    适用：值班工程师

## 第 1 章 值班与交接
1.1 值班班次分为早班（08:00-16:00）、晚班（16:00-24:00）、夜班（00:00-08:00）。
1.2 交接须在整点前后 15 分钟内完成，交接未完成前上一班不得离岗。
1.3 值班记录须包含：变更事项、告警处置、遗留问题、临时授权账号。

## 第 2 章 监控与告警
2.1 一级告警（核心链路不可用）触发后 5 分钟内电话通知值班经理。
2.2 二级告警指接口错误率连续 5 分钟超过 1%。
2.3 三级告警指资源水位超过阈值但未影响服务。
2.4 任何告警连续出现三次以上须生成《反复告警跟踪单》。

## 第 3 章 变更操作
3.1 生产变更必须在变更窗口进行，窗口为每周二、周四 20:00 至 24:00。
3.2 变更前须提交变更单并完成双人复核（执行人 + 复核人）。
3.3 变更执行前必须确认回滚脚本可用；无法回滚的变更一律不予批准。
3.4 变更完成后须观察 30 分钟并在变更单填写验证结论。

## 第 4 章 故障处置
4.1 处置顺序：先止血、后定位、再根治；不得以定位为由延误止血。
4.2 止血手段包括流量切换、版本回滚、功能降级三种。
4.3 回滚决策由值班经理作出，无需等待全部技术论证。
4.4 处置过程全程在故障群同步，每 15 分钟更新一次进展。

## 第 5 章 数据操作
5.1 涉及生产数据的写操作须提交 SQL 评审并有数据 owner 书面确认。
5.2 批量更新超过 1 万行的必须分批执行并保留回滚数据集。
5.3 禁止直连生产库执行临时脚本；确需执行的须经数据保护官批准。

## 第 6 章 备份与恢复
6.1 核心业务库每日全量备份一次，增量备份每 15 分钟一次。
6.2 备份保留期为 30 天，跨季度首周的备份额外保留 1 年。
6.3 恢复演练每半年开展一次，演练结果报分管副总裁。

## 第 7 章 账号与权限
7.1 生产权限按最小必要原则申请，最长有效期 90 天。
7.2 临时账号在完成操作后 24 小时内必须回收。
7.3 权限复核每季度一次，复核由 IT 部组织、用人部门确认。

## 第 8 章 应急处置联系人
8.1 网络：张宏（厂商 7x24 支持电话 400-820-1180）。
8.2 数据库：值班 DBA（内部短号 6612）。
8.3 安全事件：安全应急中心值班电话 0591-8888-6690。
"""

MD_SPEC_DATA_WAREHOUSE = """# 数据仓库建设规范

版本：v3.1    发布部门：数据部    生效日期：2026-03-01

## 第 1 章 分层约定
1.1 仓库分 ODS / DWD / DWS / ADS 四层，禁止跨层直连取数。
1.2 ODS 层保留源库原始结构，仅做编码统一与时区归一。
1.3 ADS 层只对应用暴露，必须配套数据口径说明。

## 第 2 章 命名规范
2.1 表名格式为「层前缀_主题域_业务对象_粒度后缀」，如 dws_trade_order_df。
2.2 字段一律小写下划线，不得使用拼音缩写以外的中文或空格。
2.3 时间分区字段统一命名为 pt，格式为 yyyy-MM-dd。

## 第 3 章 建模要求
3.1 事实表必须声明粒度；同一事实表内粒度必须唯一。
3.2 维度表须包含代理键、自然键与生效起止时间。
3.3 缓慢变化维采用拉链表实现，保留生效开始与结束时间。

## 第 4 章 调度与依赖
4.1 任务须声明产出表与上游依赖，禁止隐式依赖（轮询等待上游表）。
4.2 基线任务必须在每日 06:30 前完成，超时自动升级通知。
4.3 任务失败自动重试最多 2 次，重试间隔 5 分钟，仍失败则置手动修复状态。

## 第 5 章 质量卡点
5.1 核心表须配置四类卡点：非空、唯一性、波动率、行数环比。
5.2 波动率超过 30% 的任务自动阻断下游并通知责任人。
5.3 质量结果写入质量看板，周度汇总由数据 owner 签收。

## 第 6 章 安全与脱敏
6.1 手机号、身份证、银行卡字段在 DWD 层以下一律脱敏。
6.2 敏感字段查询须具备数据权限，取数留痕保留 180 天。
6.3 对外提供的明细数据须经数据保护官审批。
"""

TXT_NOTES_WEEKLY_OPS = """运维周会纪要（2026-09-07）
===========================

参会：运维值班组、DBA、安全应急中心

1. 本周共处理告警 37 条，其中二级告警 4 条，均已闭环。
2. 下周停机维护窗口：2026-09-12 22:00 至 23:30，影响范围为报表与导出功能。
3. 遗留问题 3 项：日志采集延迟、备份校验脚本误报、测试环境证书到期。
4. 遗留问题责任人：王倩（运维），闭环时间要求 2026-09-19 前。
5. 决议：自本月起变更窗口增加周五 22:00 至 23:00 一个补充窗口。
"""

TXT_NOTES_STRATEGY_RETREAT = """2026 年度战略务虚会纪要
=========================

时间：2026-08-14 至 2026-08-15
地点：福州·闽江畔会议中心
参会：经营管理层、各业务负责人

一、外部环境判断
1. 行业整体进入降本增效阶段，客户对交付周期与总拥有成本更为敏感。
2. 大模型能力外溢带来新的产品形态机会，但同质化竞争加剧。

二、2027 三条主线
主线一：把核心产品从项目制交付转向订阅制交付。
主线二：把行业解决方案沉淀为可复制的标准产品线。
主线三：把内部 AI 能力平台化，优先支撑客服与研发两个场景。

三、资源安排
1. AI 算力预算预留 1,500 万元，按季度滚动评审拨付。
2. 人力增量以内部转岗为主，外部招聘聚焦平台与安全两个方向。
3. 海外方向先做新加坡单一据点，年内不铺第二国。

四、待办
1. 各业务负责人于 2026-09-05 前提交主线分解计划。
2. 财务于 2026-09-15 前提交三条主线的分年财务模型。
3. 战略解码会定于 2026-09-25 召开。
"""

TXT_FAQ_IT_HELPDESK = """IT 帮助中心 FAQ
=================

1. 密码重置：通过自助门户 https://it.help.xingchenyun.com 重置，
   或拨打 IT 热线 0591-8888-6600。
2. 服务响应：热线接入后 15 分钟内响应，一般问题当日解决。
3. VPN 申请：须由部门负责人审批，开通后有效期 180 天。
4. 邮箱容量：默认 50GB，超过 80% 使用时自动提醒。
5. 终端故障：硬件故障报修后 1 个工作日内上门检测。
"""

TXT_DICT_ORDER_DOMAIN = """数据字典 · 订单域（v2026.07）
==============================

o_order_no    订单号，格式：OR + 14 位时间戳 + 4 位流水号（示例 OR202606151023004312）。
o_status      订单状态，枚举：待支付 / 已支付 / 已发货 / 已完成 / 已退款。
o_amount      订单金额，单位：分，整数，含税。
o_pay_channel 支付渠道，枚举：wechat / alipay / unionpay / corporate。
o_refundable  是否可退，布尔值，虚拟商品默认 false。
"""

TXT_SOP_ACCESS_REVIEW = """权限季度复核 SOP（IT 部）
===========================

编号：XC-SOP-IT-2025-019

1. 复核周期：每季度开展一次，覆盖全部特权账号（含数据库管理员、
   生产主机登录、加密密钥托管三类）。
2. 回收时限：员工转岗或离职当日完成权限回收，临时账号完成操作后
   24 小时内回收。
3. 复核方式：由 IT 部导出权限清单 → 用人部门主管确认 → 差异项 3 个工作日内处理。
4. 留痕：复核记录（含确认人、处理结论）保存 2 年，供审计抽查。
"""

TXT_SOP_CHANGE_MANAGEMENT = """变更管理 SOP（研发与运维）
===============================

编号：XC-SOP-RD-2025-021

1. 变更分级：分为标准变更、常规变更、紧急变更三级。
2. 紧急变更：可先行处置，但须在处置完成后 24 小时内补齐审批。
3. 评审会议：每月第二个周三下午 14:00 召开变更评审会，未通过评审的
   变更不得进入下一周窗口。
4. 失败回滚：变更后若出现 P1、P2 级异常，15 分钟内执行回滚并通报。
5. 记录：变更单编号格式为 CHG + 年月 + 3 位流水，须关联配置项。
"""

TXT_POLICY_DATA_CLASSIFICATION = """数据分类分级标准（v2026.01）
================================

发布部门：数据安全委员会

1. 分类：按业务域分为客户数据、交易数据、产品数据、员工数据、运营数据。
2. 分级：分为公开、内部、敏感、核心四级。
3. 核心数据：须加密存储（AES-256），访问实名到个人并全程留痕。
4. 共享：敏感及以上数据跨部门共享须经数据保护官审批。
5. 出境：受控数据出境须按《数据出境管理办法》完成安全评估后执行。
6. 与密级的关系：本分级用于确定存储与共享的保护强度，属分类分级维度；
   对外导出的审批权限仍按《信息安全管理制度》的密级约定执行，二者不得混用。
"""

MD_POLICY_INFOSEC_V2 = """# 信息安全管理制度（V2）

制度编号：XC-IT-2026-009
发布部门：数据安全委员会
发布日期：2026-06-10
生效日期：2026-07-01，本版本施行后原 V1 版（2025-01-01 起施行）同时废止。

## 1. 密级划分
密级仍为 public（公开）、internal（内部）、confidential（机密）三级，
分级命名的沿用不影响既有审批链路。

## 2. 数据导出
internal 及以上密级数据导出仍须数据保护官书面审批，审批记录留存 3 年。

## 3. 权限回收
员工离岗当日回收全部权限，临时账号在操作完成后 24 小时内回收。

## 4. 本次修订内容
4.1 新增远程终端管控：接入内网的个人终端须安装统一终端管控代理并开启全盘加密。
4.2 新增第三方接入管理：外包与供应商人员一律使用受限账号，操作全程录屏留痕。
4.3 新增生成式 AI 工具管控：禁止将 confidential 级数据输入外部大模型服务。
4.4 收紧移动介质管控：U 盘等移动存储介质在生产区一律禁用，确需使用的须备案。

## 5. 例外审批
因业务需要的例外情形，须由 IT 部与数据保护官双签，有效期不超过 90 天。
"""

# ---------------------------------------------------------------- dataset 用例

def A(doc_id, doc_ids, key_facts, qtype, refuse=False, reason=None,
      chunk_ids=None, anchors=None, vreq=None, perms=("general",)):
    """构造 §4 八字段标注。

    perms 语义（2026-09-17 拍板，消费方 backend/rag/permissions.py +
    评测 runner 权限门禁）：请求者**持有**的权限集合，general 隐式开放；
    文档侧所需权限见 DOCS 清单各 doc(perms=...)。权限拒答用例（RD-031）
    标 general = 无特权用户问受限文档 → 预期拒答。
    """
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
          "schema_version": "1.0-probe", "kb_id": "rag_100_docs"}
    if group:
        md["group"] = group
    return md

def derive_expected(case: dict) -> dict:
    """§4 annotation → 评测器执行契约（backend.evaluation runner 消费的 expected）。

    双 schema 并存：annotation = 任务书 §4 契约（权威标注），expected = harness
    执行契约（由 annotation 派生，勿手工编辑）。expected_doc_ids 直接以语义
    slug 传递——DocIdResolver 判分时经 registry 桥接 slug ↔ 文件名。
    version_requirement 消费方（2026-09-17）：any 直接放行；as_of/current/
    all_versions 由 runner 校验契约并透传 rejection 留痕，检索期 enforcement
    依赖 R4 版本治理字段。permission_scope 消费方已落地（权限门禁）。
    version_requirement / permission_scope 暂无消费方（§5 门禁全量字段阶段接入）。
    """
    a = case.get("annotation") or {}
    # expected_answer：key_facts 拼接为参考答案，供 LLM-as-judge（--judge）与
    # RAGAS ground_truth（ContextRecall/ContextPrecision）消费；
    # 拒答用例不给参考答案（judge 门禁自动跳过，不对拒答场景评分）。
    key_facts = a.get("key_facts") or []
    expected_answer = "；".join(key_facts) if key_facts and not a.get("should_refuse") else ""
    return {
        "relevant_docs": a.get("expected_doc_ids") or [],
        "relevant_chunks": a.get("expected_chunk_ids") or [],
        "match_type": "chunk_id",
        "min_relevant_chunks": 1,
        "required_facts": key_facts,
        "expected_answer": expected_answer,
        "should_reject": bool(a.get("should_refuse")),
    }


VREQ_AS_OF_202508 = {"type": "as_of", "date": "2025-08-01"}
VREQ_AS_OF_202406 = {"type": "as_of", "date": "2024-06-01"}
VREQ_CURRENT = {"type": "current"}
VREQ_CHAIN = {"type": "all_versions",
              "supersedes_chain": ["policy_travel_v1", "policy_travel_v2", "policy_travel_v3"]}

# ---- 扩容新增的版本约束：三条新版本链（incident / expense / infosec）----
VREQ_AS_OF_202602 = {"type": "as_of", "date": "2026-02-01"}
VREQ_AS_OF_202603 = {"type": "as_of", "date": "2026-03-01"}
VREQ_AS_OF_202501 = {"type": "as_of", "date": "2025-01-01"}
VREQ_CHAIN_INCIDENT = {"type": "all_versions",
                       "supersedes_chain": ["sop_incident_response_v1",
                                            "sop_incident_response_v2",
                                            "sop_incident_response_v3"]}
VREQ_CHAIN_EXPENSE = {"type": "all_versions",
                      "supersedes_chain": ["policy_expense_v1", "policy_expense_v2",
                                           "policy_expense_v3"]}

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
                     ["B2层机房", "90天（2026-07-01至2026-09-28）"], "exact_id",
                     perms=("general", "it_admin")),
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
                     perms=("general",)),
     "metadata": M("pdf", "report", difficulty="medium", no_answer=True)},

    # ============ 扩容新增用例（配套 27 → 100 份语料；RD-037 起）============
    # ---- Legal：法务类（md）----
    {"id": "RD-037", "kb_id": KB_ID, "module": "rag",
     "question": "公司与深蓝智能签订的双向保密协议，保密期限是多长？",
     "annotation": A("legal_nnn_agreement", ["legal_nnn_agreement"], ["自签署之日起5年"],
                     "exact_id"),
     "metadata": M("md", "legal")},
    {"id": "RD-038", "kb_id": KB_ID, "module": "rag",
     "question": "违反双向保密协议要支付多少违约金？",
     "annotation": A("legal_nnn_agreement", ["legal_nnn_agreement"], ["50万元"], "exact_id"),
     "metadata": M("md", "legal")},
    {"id": "RD-039", "kb_id": KB_ID, "module": "rag",
     "question": "知识产权转让的总价是多少？付款怎么安排？",
     "annotation": A("legal_ip_assignment", ["legal_ip_assignment"],
                     ["80万元", "签署后15个工作日内付50%，权属变更登记完成后付剩余50%"],
                     "multi_condition"),
     "metadata": M("md", "legal")},
    {"id": "RD-040", "kb_id": KB_ID, "module": "rag",
     "question": "受让的发明专利与软著，权属变更登记什么时候完成？",
     "annotation": A("legal_ip_assignment", ["legal_ip_assignment"], ["2025-11-20"], "exact_id"),
     "metadata": M("md", "legal")},
    {"id": "RD-041", "kb_id": KB_ID, "module": "rag",
     "question": "向中科智联采购了多少台 GPU 服务器？合同总价多少？",
     "annotation": A("legal_purchase_contract_zhongke", ["legal_purchase_contract_zhongke"],
                     ["12台", "468万元"], "multi_condition",
                     anchors=["合同编号：XC-CG-2026-014"]),
     "metadata": M("md", "legal", group="cross_procurement")},
    {"id": "RD-042", "kb_id": KB_ID, "module": "rag",
     "question": "中科智联如果逾期交货，违约金怎么算？有没有上限？",
     "annotation": A("legal_purchase_contract_zhongke", ["legal_purchase_contract_zhongke"],
                     ["每逾期一日按合同总价0.5%", "累计不超过合同总价的5%"], "multi_condition"),
     "metadata": M("md", "legal", difficulty="hard", group="cross_procurement")},
    {"id": "RD-043", "kb_id": KB_ID, "module": "rag",
     "question": "争议和解备忘录约定公司一次性支付多少钱？",
     "annotation": A("legal_settlement_memo", ["legal_settlement_memo"], ["35万元"], "exact_id"),
     "metadata": M("md", "legal")},

    # ---- Policy：制度类（md）----
    {"id": "RD-044", "kb_id": KB_ID, "module": "rag",
     "question": "受控物项清单多久更新一次？出口管制筛查记录要保存多久？",
     "annotation": A("policy_export_control", ["policy_export_control"],
                     ["每季度更新一次", "保存10年"], "multi_condition"),
     "metadata": M("md", "policy")},
    {"id": "RD-045", "kb_id": KB_ID, "module": "rag",
     "question": "没做出口管制筛查就签了合同，直接责任人会有什么后果？",
     "annotation": A("policy_export_control", ["policy_export_control"],
                     ["当年度绩效一票否决"], "exact_id"),
     "metadata": M("md", "policy", difficulty="hard")},
    {"id": "RD-046", "kb_id": KB_ID, "module": "rag",
     "question": "招聘新增编制（HC）由谁终审？内部推荐成功奖励多少钱？",
     "annotation": A("policy_recruitment", ["policy_recruitment"],
                     ["CEO终审", "8,000元/人"], "multi_condition"),
     "metadata": M("md", "policy", group="cross_hiring")},
    {"id": "RD-047", "kb_id": KB_ID, "module": "rag",
     "question": "发 Offer 之前必须完成什么流程？核心岗位有什么要求？",
     "annotation": A("policy_recruitment", ["policy_recruitment"],
                     ["背景调查", "核心岗位背调覆盖率100%"], "multi_condition"),
     "metadata": M("md", "policy", group="cross_hiring")},
    {"id": "RD-048", "kb_id": KB_ID, "module": "rag",
     "question": "绩效考核结果的强制分布是怎么规定的？",
     "annotation": A("policy_performance_appraisal", ["policy_performance_appraisal"],
                     ["A档不超过20%", "D档不低于5%"], "multi_condition"),
     "metadata": M("md", "policy")},
    {"id": "RD-049", "kb_id": KB_ID, "module": "rag",
     "question": "连续两次绩效考核为 D 会怎么处理？",
     "annotation": A("policy_performance_appraisal", ["policy_performance_appraisal"],
                     ["进入绩效改进计划PIP", "改进期3个月"], "multi_condition"),
     "metadata": M("md", "policy", difficulty="medium")},
    {"id": "RD-050", "kb_id": KB_ID, "module": "rag",
     "question": "生产环境软件正版化率目标是多少？全公司软件盘点在什么时候？",
     "annotation": A("policy_software_asset", ["policy_software_asset"],
                     ["正版化率100%", "每年12月"], "multi_condition"),
     "metadata": M("md", "policy")},
    {"id": "RD-051", "kb_id": KB_ID, "module": "rag",
     "question": "V1 版费用报销办法的报销时限是多长？审批要过几级？",
     "annotation": A("policy_expense_v1", ["policy_expense_v1"],
                     ["60日内", "部门负责人→财务部两级审批"], "multi_condition",
                     vreq=VREQ_AS_OF_202602),
     "metadata": M("md", "policy", difficulty="hard", group="version_chain")},
    {"id": "RD-052", "kb_id": KB_ID, "module": "rag",
     "question": "研发中心每月加班上限是多少小时？工作日加班费怎么计发？",
     "annotation": A("policy_overtime_rnd", ["policy_overtime_rnd"],
                     ["每月不超过36小时", "工作日按1.5倍计发"], "multi_condition"),
     "metadata": M("md", "policy", group="similar_pair")},
    {"id": "RD-053", "kb_id": KB_ID, "module": "rag",
     "question": "客服部法定节假日加班怎么计发？夜班津贴多少钱？",
     "annotation": A("policy_overtime_cs", ["policy_overtime_cs"],
                     ["法定节假日按3倍工资计发", "夜班每班次60元"], "multi_condition"),
     "metadata": M("md", "policy", group="similar_pair")},
    {"id": "RD-054", "kb_id": KB_ID, "module": "rag",
     "question": "研发中心和客服部，每月加班工时上限分别是多少？",
     "annotation": A("policy_overtime_rnd",
                     ["policy_overtime_rnd", "policy_overtime_cs"],
                     ["研发中心36小时", "客服部24小时"], "low_confidence"),
     "metadata": M("md", "policy", difficulty="hard", group="similar_pair")},
    {"id": "RD-055", "kb_id": KB_ID, "module": "rag",
     "question": "V2 版信息安全管理制度相比旧版，新增了哪些管控要求？",
     "annotation": A("policy_infosec_v2", ["policy_infosec_v2"],
                     ["远程终端须装管控代理并全盘加密", "第三方人员使用受限账号并录屏留痕",
                      "禁止将confidential级数据输入外部大模型", "禁用移动存储介质"],
                     "multi_condition", vreq=VREQ_CURRENT),
     "metadata": M("md", "policy", difficulty="hard", group="version_chain")},
    {"id": "RD-056", "kb_id": KB_ID, "module": "rag",
     "question": "按现行信息安全制度，数据对外导出须经谁审批？",
     "annotation": A("policy_infosec_v2", ["policy_infosec_v2"],
                     ["数据保护官书面审批"], "multi_condition", vreq=VREQ_CURRENT),
     "metadata": M("md", "policy", difficulty="hard", group="version_chain")},
    {"id": "RD-057", "kb_id": KB_ID, "module": "rag",
     "question": "客户退款审核要在多久内完成？超过 5 万元怎么办？",
     "annotation": A("sop_refund_processing", ["sop_refund_processing"],
                     ["3个工作日内", "超过5万元报财务总监审批"], "multi_condition"),
     "metadata": M("md", "sop")},
    {"id": "RD-058", "kb_id": KB_ID, "module": "rag",
     "question": "财务付款之后，客户多久能收到退款？",
     "annotation": A("sop_refund_processing", ["sop_refund_processing"],
                     ["7至15个工作日"], "exact_id"),
     "metadata": M("md", "sop")},
    {"id": "RD-059", "kb_id": KB_ID, "module": "rag",
     "question": "供应商准入要提交几项材料？要不要交保证金？",
     "annotation": A("sop_vendor_onboarding", ["sop_vendor_onboarding"],
                     ["5项材料", "履约保证金10万元"], "multi_condition"),
     "metadata": M("md", "sop", group="cross_procurement")},
    {"id": "RD-060", "kb_id": KB_ID, "module": "rag",
     "question": "V1 版故障应急响应 SOP 里，P1 级故障要求多久恢复服务？",
     "annotation": A("sop_incident_response_v1", ["sop_incident_response_v1"],
                     ["4小时内"], "exact_id", vreq=VREQ_AS_OF_202501),
     "metadata": M("md", "sop", difficulty="hard", group="version_chain")},

    # ---- Report / Manual / Spec：长文与多页（md）----
    {"id": "RD-061", "kb_id": KB_ID, "module": "rag",
     "question": "2026 年第二季度共处置了多少起安全事件？其中 P1 级几起？",
     "annotation": A("report_incident_2026q2", ["report_incident_2026q2"],
                     ["14起", "P1级1起"], "multi_condition"),
     "metadata": M("md", "report", group="cross_incident")},
    {"id": "RD-062", "kb_id": KB_ID, "module": "rag",
     "question": "5 月 19 日网关鉴权绕行事件影响了多少个账户？",
     "annotation": A("report_incident_2026q2", ["report_incident_2026q2"],
                     ["1,243个账户"], "exact_id"),
     "metadata": M("md", "report", group="cross_incident")},
    {"id": "RD-063", "kb_id": KB_ID, "module": "rag",
     "question": "2026Q2 安全事件的平均检测时长是多少？",
     "annotation": A("report_incident_2026q2", ["report_incident_2026q2"],
                     ["23分钟"], "table_value"),
     "metadata": M("md", "report", group="cross_incident")},
    {"id": "RD-064", "kb_id": KB_ID, "module": "rag",
     "question": "生产环境变更窗口定在什么时候？",
     "annotation": A("manual_ops_runbook", ["manual_ops_runbook"],
                     ["每周二、周四20:00至24:00"], "exact_id"),
     "metadata": M("md", "manual")},
    {"id": "RD-065", "kb_id": KB_ID, "module": "rag",
     "question": "核心业务库的备份保留多久？跨季度首周的备份呢？",
     "annotation": A("manual_ops_runbook", ["manual_ops_runbook"],
                     ["保留30天", "跨季度首周额外保留1年"], "multi_condition"),
     "metadata": M("md", "manual", difficulty="medium")},
    {"id": "RD-066", "kb_id": KB_ID, "module": "rag",
     "question": "一级告警触发后，多久要电话通知值班经理？",
     "annotation": A("manual_ops_runbook", ["manual_ops_runbook"], ["5分钟内"], "exact_id"),
     "metadata": M("md", "manual", difficulty="hard")},
    {"id": "RD-067", "kb_id": KB_ID, "module": "rag",
     "question": "数据仓库分哪几层？能不能跨层直接取数？",
     "annotation": A("spec_data_warehouse", ["spec_data_warehouse"],
                     ["ODS/DWD/DWS/ADS四层", "禁止跨层直连取数"], "multi_condition"),
     "metadata": M("md", "spec")},
    {"id": "RD-068", "kb_id": KB_ID, "module": "rag",
     "question": "数仓基线任务须在什么时间前完成？任务失败后重试几次？",
     "annotation": A("spec_data_warehouse", ["spec_data_warehouse"],
                     ["每日06:30前", "最多重试2次，间隔5分钟"], "multi_condition"),
     "metadata": M("md", "spec")},
    {"id": "RD-069", "kb_id": KB_ID, "module": "rag",
     "question": "核心表质量卡点里，波动率超过多少会自动阻断下游？",
     "annotation": A("spec_data_warehouse", ["spec_data_warehouse"], ["超过30%"], "exact_id"),
     "metadata": M("md", "spec", difficulty="medium")},
    # ---- DOCX：11 份 ----
    {"id": "RD-070", "kb_id": KB_ID, "module": "rag",
     "question": "劳动合同首次签订期限多久？试用期多久？",
     "annotation": A("legal_labor_contract_template", ["legal_labor_contract_template"],
                     ["首次3年", "试用期6个月"], "multi_condition"),
     "metadata": M("docx", "legal")},
    {"id": "RD-071", "kb_id": KB_ID, "module": "rag",
     "question": "竞业限制最长能约定多久？补偿金按什么标准支付？",
     "annotation": A("legal_labor_contract_template", ["legal_labor_contract_template"],
                     ["不超过2年", "离职前十二个月平均工资的30%按月支付"], "multi_condition"),
     "metadata": M("docx", "legal", difficulty="medium")},
    {"id": "RD-072", "kb_id": KB_ID, "module": "rag",
     "question": "DPA 里乙方使用子处理者，须提前多久告知甲方？",
     "annotation": A("legal_dpa_processing", ["legal_dpa_processing"], ["15个工作日"],
                     "exact_id"),
     "metadata": M("docx", "legal", group="cross_data_compliance")},
    {"id": "RD-073", "kb_id": KB_ID, "module": "rag",
     "question": "服务终止后，乙方须在多长时间内删除数据？",
     "annotation": A("legal_dpa_processing", ["legal_dpa_processing"], ["90天内"], "exact_id"),
     "metadata": M("docx", "legal", group="cross_data_compliance")},
    {"id": "RD-074", "kb_id": KB_ID, "module": "rag",
     "question": "公司在办的诉讼与仲裁案件共几起？标的额最高的是多少？",
     "annotation": A("legal_case_litigation_2026", ["legal_case_litigation_2026"],
                     ["3起", "1,180万元"], "multi_condition",
                     perms=("general", "legal_confidential")),
     "metadata": M("docx", "legal", difficulty="hard")},
    {"id": "RD-075", "kb_id": KB_ID, "module": "rag",
     "question": "V2 版故障 SOP 的 P1 恢复时限是多少？故障通报走哪些渠道？",
     "annotation": A("sop_incident_response_v2", ["sop_incident_response_v2"],
                     ["2小时内", "邮件+短信+企业微信三通道"], "multi_condition"),
     "metadata": M("docx", "sop", difficulty="hard", group="version_chain")},
    {"id": "RD-076", "kb_id": KB_ID, "module": "rag",
     "question": "版本发布的灰度比例怎么推进？每个阶段要观察多久？",
     "annotation": A("sop_release_deploy", ["sop_release_deploy"],
                     ["5%→20%→100%", "每阶段观察不少于30分钟"], "multi_condition"),
     "metadata": M("docx", "sop")},
    {"id": "RD-077", "kb_id": KB_ID, "module": "rag",
     "question": "什么情况下必须回滚？回滚要多久执行完？",
     "annotation": A("sop_release_deploy", ["sop_release_deploy"],
                     ["出现P1/P2级缺陷或核心接口错误率超过1%", "决策后15分钟内执行完毕"],
                     "multi_condition"),
     "metadata": M("docx", "sop", difficulty="medium")},
    {"id": "RD-078", "kb_id": KB_ID, "module": "rag",
     "question": "备份恢复演练的 RTO 和 RPO 目标分别是多少？",
     "annotation": A("sop_backup_recovery_drill", ["sop_backup_recovery_drill"],
                     ["RTO 4小时", "RPO 15分钟"], "multi_condition"),
     "metadata": M("docx", "sop")},
    {"id": "RD-079", "kb_id": KB_ID, "module": "rag",
     "question": "最近一次备份恢复演练是哪天？实际恢复耗时多久？",
     "annotation": A("sop_backup_recovery_drill", ["sop_backup_recovery_drill"],
                     ["2026-05-20", "2小时48分"], "exact_id"),
     "metadata": M("docx", "sop")},
    {"id": "RD-080", "kb_id": KB_ID, "module": "rag",
     "question": "常规岗位要面试几轮？面评要在多久内提交？",
     "annotation": A("sop_interview_hiring", ["sop_interview_hiring"],
                     ["3轮（技术面、主管面、HRBP面）", "24小时内提交"], "multi_condition"),
     "metadata": M("docx", "sop", group="cross_hiring")},
    {"id": "RD-081", "kb_id": KB_ID, "module": "rag",
     "question": "V2 版报销办法的报销时限是多少？超过 5 万元怎么办？",
     "annotation": A("policy_expense_v2", ["policy_expense_v2"],
                     ["30日内", "单笔超过5万元加签财务总监"], "multi_condition"),
     "metadata": M("docx", "policy", difficulty="hard", group="version_chain")},
    {"id": "RD-082", "kb_id": KB_ID, "module": "rag",
     "question": "V2 版报销办法对电子发票新增了什么要求？",
     "annotation": A("policy_expense_v2", ["policy_expense_v2"],
                     ["电子发票须通过系统查重", "重复报销一律退回并通报"], "multi_condition"),
     "metadata": M("docx", "policy", group="version_chain")},
    {"id": "RD-083", "kb_id": KB_ID, "module": "rag",
     "question": "2025 年末付费客户数是多少？NPS 多少？",
     "annotation": A("report_product_annual_2025", ["report_product_annual_2025"],
                     ["1,842家", "NPS 46"], "multi_condition"),
     "metadata": M("docx", "report", group="cross_product")},
    {"id": "RD-084", "kb_id": KB_ID, "module": "rag",
     "question": "数据接入平台 1.0 是哪天发布的？",
     "annotation": A("report_product_annual_2025", ["report_product_annual_2025"],
                     ["2025-09-12"], "exact_id"),
     "metadata": M("docx", "report", group="cross_product")},
    {"id": "RD-085", "kb_id": KB_ID, "module": "rag",
     "question": "董事会批准的 2026 年度资本性支出预算是多少？",
     "annotation": A("report_board_resolution_2026", ["report_board_resolution_2026"],
                     ["3,200万元"], "exact_id"),
     "metadata": M("docx", "report")},
    {"id": "RD-086", "kb_id": KB_ID, "module": "rag",
     "question": "新加坡子公司首期注册资本多少？管理层授权有效期多久？",
     "annotation": A("report_board_resolution_2026", ["report_board_resolution_2026"],
                     ["200万新元", "12个月"], "multi_condition"),
     "metadata": M("docx", "report", difficulty="medium")},
    {"id": "RD-087", "kb_id": KB_ID, "module": "rag",
     "question": "单笔超过 1 万元的报销有什么额外要求？",
     "annotation": A("faq_finance_reimbursement", ["faq_finance_reimbursement"],
                     ["须附合同或采购审批说明"], "exact_id"),
     "metadata": M("docx", "faq")},
    {"id": "RD-088", "kb_id": KB_ID, "module": "rag",
     "question": "报销单被退回后，同一单还能重新提交几次？",
     "annotation": A("faq_finance_reimbursement", ["faq_finance_reimbursement"], ["3次"],
                     "exact_id"),
     "metadata": M("docx", "faq")},

    # ---- PDF：单栏文本 6 份 ----
    {"id": "RD-089", "kb_id": KB_ID, "module": "rag",
     "question": "星辰盾企业版软件许可授权多少席位？年许可费多少？",
     "annotation": A("legal_software_license", ["legal_software_license"],
                     ["500个命名用户席位", "年许可费96万元"], "multi_condition"),
     "metadata": M("pdf", "legal")},
    {"id": "RD-090", "kb_id": KB_ID, "module": "rag",
     "question": "软件许可的期限是哪一段？",
     "annotation": A("legal_software_license", ["legal_software_license"],
                     ["2026-01-01至2028-12-31"], "exact_id"),
     "metadata": M("pdf", "legal")},
    {"id": "RD-091", "kb_id": KB_ID, "module": "rag",
     "question": "电子介质的数据怎么销毁？销毁记录保存多久？",
     "annotation": A("sop_data_destruction", ["sop_data_destruction"],
                     ["逻辑覆写3次后消磁", "保存5年"], "multi_condition"),
     "metadata": M("pdf", "sop")},
    {"id": "RD-092", "kb_id": KB_ID, "module": "rag",
     "question": "按现行报销办法，费用发生后须在多久内提交报销单？",
     "annotation": A("policy_expense_v3", ["policy_expense_v3"], ["20个工作日内"],
                     "multi_condition", vreq=VREQ_CURRENT),
     "metadata": M("pdf", "policy", difficulty="hard", group="version_chain")},
    {"id": "RD-093", "kb_id": KB_ID, "module": "rag",
     "question": "现行报销办法下，单笔金额达到多少就须附合同或验收说明？",
     "annotation": A("policy_expense_v3", ["policy_expense_v3"], ["5,000元"], "exact_id",
                     vreq=VREQ_CURRENT),
     "metadata": M("pdf", "policy", group="version_chain")},
    {"id": "RD-094", "kb_id": KB_ID, "module": "rag",
     "question": "每人每月最多可以远程办公几天？核心在线时段是什么时候？",
     "annotation": A("policy_remote_work", ["policy_remote_work"],
                     ["不超过6天", "10:00至16:00"], "multi_condition"),
     "metadata": M("pdf", "policy")},
    {"id": "RD-095", "kb_id": KB_ID, "module": "rag",
     "question": "2026 年 Q2 全链路压测的峰值 TPS 是多少？超出目标多少？",
     "annotation": A("report_performance_test_2026", ["report_performance_test_2026"],
                     ["3,860", "超出目标28.7%"], "table_value"),
     "metadata": M("pdf", "report")},
    {"id": "RD-096", "kb_id": KB_ID, "module": "rag",
     "question": "压测中下单接口的 P99 延迟是多少毫秒？",
     "annotation": A("report_performance_test_2026", ["report_performance_test_2026"],
                     ["218毫秒"], "table_value"),
     "metadata": M("pdf", "report")},
    {"id": "RD-097", "kb_id": KB_ID, "module": "rag",
     "question": "年度运维服务合同的年度服务费是多少？P1 故障的响应与恢复要求？",
     "annotation": A("contract_annual_maintenance", ["contract_annual_maintenance"],
                     ["128万元", "15分钟内响应、2小时内恢复"], "multi_condition",
                     perms=("general", "finance_restricted")),
     "metadata": M("pdf", "contract", difficulty="medium")},

    # ---- PDF：复杂版面 7 份（双栏 + 表格 + 页眉页脚页码）----
    {"id": "RD-098", "kb_id": KB_ID, "module": "rag",
     "question": "2025 年公司营业收入和净利润分别是多少？",
     "annotation": A("layout_report_fin_annual_2025", ["layout_report_fin_annual_2025"],
                     ["4.12亿元", "6,840万元"], "multi_condition",
                     perms=("general", "finance_restricted")),
     "metadata": M("pdf", "report", difficulty="medium", group="cross_finance")},
    {"id": "RD-099", "kb_id": KB_ID, "module": "rag",
     "question": "2025 年研发投入是多少？占营业收入多少？",
     "annotation": A("layout_report_fin_annual_2025", ["layout_report_fin_annual_2025"],
                     ["1.02亿元", "24.8%"], "multi_condition",
                     perms=("general", "finance_restricted")),
     "metadata": M("pdf", "report", difficulty="hard", group="cross_finance")},
    {"id": "RD-100", "kb_id": KB_ID, "module": "rag",
     "question": "2025 年度合并利润表 3-1 里，营业成本是多少？占营业收入多少？",
     "annotation": A("layout_report_fin_annual_2025", ["layout_report_fin_annual_2025"],
                     ["17,140万元", "占营业收入41.6%"], "table_value",
                     perms=("general", "finance_restricted")),
     "metadata": M("pdf", "report", difficulty="hard", group="cross_finance")},
    {"id": "RD-101", "kb_id": KB_ID, "module": "rag",
     "question": "员工手册里通讯补贴多少钱？年度体检安排在什么时候？",
     "annotation": A("layout_manual_employee_handbook", ["layout_manual_employee_handbook"],
                     ["每人每月150元", "每年9月至10月"], "multi_condition"),
     "metadata": M("pdf", "manual", difficulty="medium")},
    {"id": "RD-102", "kb_id": KB_ID, "module": "rag",
     "question": "每人每年须完成多少学时的学习任务？哪类课程是必修？",
     "annotation": A("layout_manual_employee_handbook", ["layout_manual_employee_handbook"],
                     ["不少于40学时", "合规类课程为必修"], "multi_condition"),
     "metadata": M("pdf", "manual")},
    {"id": "RD-103", "kb_id": KB_ID, "module": "rag",
     "question": "正式员工离职须提前多久书面通知？试用期内呢？",
     "annotation": A("layout_manual_employee_handbook", ["layout_manual_employee_handbook"],
                     ["正式员工提前30日", "试用期内提前3日"], "multi_condition"),
     "metadata": M("pdf", "manual", difficulty="medium")},
    {"id": "RD-104", "kb_id": KB_ID, "module": "rag",
     "question": "API 网关的单请求体上限是多少？默认超时多少？",
     "annotation": A("layout_spec_api_gateway", ["layout_spec_api_gateway"],
                     ["2MB", "3秒"], "multi_condition"),
     "metadata": M("pdf", "spec", group="cross_product")},
    {"id": "RD-105", "kb_id": KB_ID, "module": "rag",
     "question": "网关核心参数表 2-1 里，重试次数与退避策略是什么？",
     "annotation": A("layout_spec_api_gateway", ["layout_spec_api_gateway"],
                     ["重试2次", "指数退避，首次间隔200ms"], "table_value"),
     "metadata": M("pdf", "spec", difficulty="hard", group="cross_product")},
    {"id": "RD-106", "kb_id": KB_ID, "module": "rag",
     "question": "JWT 令牌有效期多久？刷新令牌呢？",
     "annotation": A("layout_spec_api_gateway", ["layout_spec_api_gateway"],
                     ["JWT令牌2小时", "刷新令牌7天"], "multi_condition"),
     "metadata": M("pdf", "spec", group="cross_product")},
    {"id": "RD-107", "kb_id": KB_ID, "module": "rag",
     "question": "评标委员会由几名成员组成？外部专家至少几名？",
     "annotation": A("layout_tender_evaluation", ["layout_tender_evaluation"],
                     ["5名成员", "外部专家不少于2名"], "multi_condition"),
     "metadata": M("pdf", "legal")},
    {"id": "RD-108", "kb_id": KB_ID, "module": "rag",
     "question": "综合评分权重表 3-1 里，技术方案、商务报价、履约能力各占多少？",
     "annotation": A("layout_tender_evaluation", ["layout_tender_evaluation"],
                     ["技术方案45%", "商务报价35%", "履约能力20%"], "table_value"),
     "metadata": M("pdf", "legal", difficulty="medium")},
    {"id": "RD-109", "kb_id": KB_ID, "module": "rag",
     "question": "2026 年度合规审查发现的高、中、低风险事项各有几项？",
     "annotation": A("legal_compliance_review_2026", ["legal_compliance_review_2026"],
                     ["高风险3项", "中风险7项", "低风险15项"], "multi_condition",
                     perms=("general", "legal_confidential")),
     "metadata": M("pdf", "legal", difficulty="hard")},
    {"id": "RD-110", "kb_id": KB_ID, "module": "rag",
     "question": "风险事项处置要求表 3-1 里，高风险事项的整改截止日是哪天？",
     "annotation": A("legal_compliance_review_2026", ["legal_compliance_review_2026"],
                     ["2026-11-30"], "table_value",
                     perms=("general", "legal_confidential")),
     "metadata": M("pdf", "legal", difficulty="hard")},
    {"id": "RD-111", "kb_id": KB_ID, "module": "rag",
     "question": "数据出境的接收方是谁？拟出境的数据规模是多少？",
     "annotation": A("legal_data_export_assessment", ["legal_data_export_assessment"],
                     ["新加坡子公司", "每年12.4万条"], "multi_condition",
                     perms=("general", "legal_confidential")),
     "metadata": M("pdf", "legal", difficulty="hard", group="cross_data_compliance")},
    {"id": "RD-112", "kb_id": KB_ID, "module": "rag",
     "question": "数据出境要素摘要表 3-1 里，评估结论有效期是多久？",
     "annotation": A("legal_data_export_assessment", ["legal_data_export_assessment"],
                     ["2年", "自2026-05-26起算"], "table_value",
                     perms=("general", "legal_confidential")),
     "metadata": M("pdf", "legal", difficulty="hard", group="cross_data_compliance")},
    {"id": "RD-113", "kb_id": KB_ID, "module": "rag",
     "question": "按现行故障 SOP，P1 级故障须多久恢复服务？首次通报不超过多久？",
     "annotation": A("sop_incident_response_v3", ["sop_incident_response_v3"],
                     ["90分钟内恢复", "首次通报不超过15分钟"], "multi_condition",
                     vreq=VREQ_CURRENT),
     "metadata": M("pdf", "sop", difficulty="hard", group="version_chain")},
    {"id": "RD-114", "kb_id": KB_ID, "module": "rag",
     "question": "故障处置 RACI 表 4-1 里，止血处置环节由谁负责、时限多少？",
     "annotation": A("sop_incident_response_v3", ["sop_incident_response_v3"],
                     ["主值/替补", "90分钟内恢复"], "table_value"),
     "metadata": M("pdf", "sop", difficulty="hard", group="version_chain")},
    {"id": "RD-115", "kb_id": KB_ID, "module": "rag",
     "question": "主值多久没响应会升级到替补？替补须在多久内接手？",
     "annotation": A("sop_incident_response_v3", ["sop_incident_response_v3"],
                     ["主值10分钟内未响应即升级", "替补须15分钟内接手"], "multi_condition"),
     "metadata": M("pdf", "sop", difficulty="hard", group="version_chain")},

    # ---- PDF：扫描件 2 份（须经 OCR 才可读）----
    {"id": "RD-116", "kb_id": KB_ID, "module": "rag",
     "question": "报销单 BX-2026-0518 的报销金额是多少？什么时候审批完成？",
     "annotation": A("scan_expense_claim_form", ["scan_expense_claim_form"],
                     ["3,480.00元", "审批完成2026-08-09"], "exact_id"),
     "metadata": M("pdf", "form", difficulty="medium", group="scanned")},
    {"id": "RD-117", "kb_id": KB_ID, "module": "rag",
     "question": "2026-06-25 那场培训，应到多少人？实到多少人？",
     "annotation": A("scan_training_signin", ["scan_training_signin"],
                     ["应到48人", "实到45人"], "exact_id"),
     "metadata": M("pdf", "form", difficulty="medium", group="scanned")},
    # ---- TXT：8 份 ----
    {"id": "RD-118", "kb_id": KB_ID, "module": "rag",
     "question": "运维周会定的下周停机维护窗口是什么时间？影响哪些功能？",
     "annotation": A("notes_weekly_ops_2026", ["notes_weekly_ops_2026"],
                     ["2026-09-12 22:00至23:30", "影响报表与导出功能"], "multi_condition"),
     "metadata": M("txt", "notes", group="cross_incident")},
    {"id": "RD-119", "kb_id": KB_ID, "module": "rag",
     "question": "本周运维周会遗留几项问题？责任人是谁？",
     "annotation": A("notes_weekly_ops_2026", ["notes_weekly_ops_2026"],
                     ["遗留问题3项", "王倩"], "multi_condition"),
     "metadata": M("txt", "notes", difficulty="hard", group="cross_incident")},
    {"id": "RD-120", "kb_id": KB_ID, "module": "rag",
     "question": "2026 年战略务虚会什么时候在哪里召开？AI 算力预算预留多少？",
     "annotation": A("notes_strategy_retreat_2026", ["notes_strategy_retreat_2026"],
                     ["2026-08-14至08-15", "福州·闽江畔会议中心", "1,500万元"],
                     "multi_condition"),
     "metadata": M("txt", "notes", difficulty="medium")},
    {"id": "RD-121", "kb_id": KB_ID, "module": "rag",
     "question": "2027 年的三条主线分别是什么？",
     "annotation": A("notes_strategy_retreat_2026", ["notes_strategy_retreat_2026"],
                     ["核心产品由项目制转向订阅制交付",
                      "行业解决方案沉淀为可复制标准产品线",
                      "内部AI能力平台化"], "low_confidence"),
     "metadata": M("txt", "notes", difficulty="hard")},
    {"id": "RD-122", "kb_id": KB_ID, "module": "rag",
     "question": "VPN 开通后有效期多久？IT 热线电话是多少？",
     "annotation": A("faq_it_helpdesk", ["faq_it_helpdesk"],
                     ["有效期180天", "0591-8888-6600"], "multi_condition"),
     "metadata": M("txt", "faq")},
    {"id": "RD-123", "kb_id": KB_ID, "module": "rag",
     "question": "订单域里 o_order_no 的编码格式是怎么规定的？",
     "annotation": A("dict_order_domain", ["dict_order_domain"],
                     ["OR + 14位时间戳 + 4位流水号"], "exact_id"),
     "metadata": M("txt", "reference")},
    {"id": "RD-124", "kb_id": KB_ID, "module": "rag",
     "question": "权限季度复核里，临时账号要在多久内回收？复核记录保存多久？",
     "annotation": A("sop_access_review", ["sop_access_review"],
                     ["操作完成后24小时内回收", "保存2年"], "multi_condition"),
     "metadata": M("txt", "sop")},
    {"id": "RD-125", "kb_id": KB_ID, "module": "rag",
     "question": "紧急变更要多久补齐审批？变更后出现 P1/P2 异常多久内回滚？",
     "annotation": A("sop_change_management", ["sop_change_management"],
                     ["处置完成后24小时内补齐审批", "15分钟内执行回滚"], "multi_condition"),
     "metadata": M("txt", "sop", difficulty="medium")},
    {"id": "RD-126", "kb_id": KB_ID, "module": "rag",
     "question": "数据分类分级标准里，数据分为哪几级？核心数据怎么存储？",
     "annotation": A("policy_data_classification", ["policy_data_classification"],
                     ["公开/内部/敏感/核心四级", "AES-256加密存储"], "multi_condition"),
     "metadata": M("txt", "policy", group="cross_data_compliance")},

    # ---- XLSX：多 Sheet 6 + 单 Sheet 4 ----
    {"id": "RD-127", "kb_id": KB_ID, "module": "rag",
     "question": "2026 年预算里营业收入和净利润的目标分别是多少？",
     "annotation": A("xlsx_finance_budget_2026", ["xlsx_finance_budget_2026"],
                     ["52,000万元", "8,100万元"], "table_value",
                     perms=("general", "finance_restricted")),
     "metadata": M("xlsx", "table", difficulty="medium", group="cross_finance")},
    {"id": "RD-128", "kb_id": KB_ID, "module": "rag",
     "question": "收入预算表里数据服务产品线的预算是多少？占比多少？",
     "annotation": A("xlsx_finance_budget_2026", ["xlsx_finance_budget_2026"],
                     ["28,600万元", "55.0%"], "table_value",
                     perms=("general", "finance_restricted")),
     "metadata": M("xlsx", "table", difficulty="hard", group="cross_finance")},
    {"id": "RD-129", "kb_id": KB_ID, "module": "rag",
     "question": "全公司编制数、在职数与缺口合计分别是多少？",
     "annotation": A("xlsx_hr_headcount_2026", ["xlsx_hr_headcount_2026"],
                     ["编制560", "在职512", "缺口48"], "table_value",
                     perms=("general", "hr_confidential")),
     "metadata": M("xlsx", "table", group="cross_hiring")},
    {"id": "RD-130", "kb_id": KB_ID, "module": "rag",
     "question": "研发序列已发 Offer 多少人？已到岗多少人？",
     "annotation": A("xlsx_hr_headcount_2026", ["xlsx_hr_headcount_2026"],
                     ["已发Offer 7", "已到岗4"], "table_value",
                     perms=("general", "hr_confidential")),
     "metadata": M("xlsx", "table", difficulty="hard")},
    {"id": "RD-131", "kb_id": KB_ID, "module": "rag",
     "question": "A1 机房有多少个节点？可用冗余多少？",
     "annotation": A("xlsx_ops_capacity_plan", ["xlsx_ops_capacity_plan"],
                     ["46个节点", "冗余28%"], "table_value",
                     perms=("general", "it_admin")),
     "metadata": M("xlsx", "table")},
    {"id": "RD-132", "kb_id": KB_ID, "module": "rag",
     "question": "容量规划表里 CPU 的平均水位、峰值水位和告警阈值分别是多少？",
     "annotation": A("xlsx_ops_capacity_plan", ["xlsx_ops_capacity_plan"],
                     ["平均63%", "峰值78%", "阈值≤70%"], "table_value",
                     perms=("general", "it_admin")),
     "metadata": M("xlsx", "table", difficulty="hard")},
    {"id": "RD-133", "kb_id": KB_ID, "module": "rag",
     "question": "2026-06 的 MAU、DAU 和付费租户数分别是多少？",
     "annotation": A("xlsx_product_metrics_2026", ["xlsx_product_metrics_2026"],
                     ["MAU 124,000", "DAU 24,600", "付费租户3,120"], "table_value"),
     "metadata": M("xlsx", "table", group="cross_product")},
    {"id": "RD-134", "kb_id": KB_ID, "module": "rag",
     "question": "收入贡献表里企业版的 ARPU 是多少？",
     "annotation": A("xlsx_product_metrics_2026", ["xlsx_product_metrics_2026"],
                     ["8,600元/月"], "table_value"),
     "metadata": M("xlsx", "table", group="cross_product")},
    {"id": "RD-135", "kb_id": KB_ID, "module": "rag",
     "question": "合同台账里与中科智联的合同金额是多少？状态如何？",
     "annotation": A("xlsx_legal_contract_register", ["xlsx_legal_contract_register"],
                     ["468万元", "履行中"], "table_value",
                     perms=("general", "legal_confidential")),
     "metadata": M("xlsx", "table", difficulty="medium", group="cross_procurement")},
    {"id": "RD-136", "kb_id": KB_ID, "module": "rag",
     "question": "合同风险清单里高风险合同几份？R-01 的责任人是谁？",
     "annotation": A("xlsx_legal_contract_register", ["xlsx_legal_contract_register"],
                     ["高风险合同3份", "陈斌"], "table_value",
                     perms=("general", "legal_confidential")),
     "metadata": M("xlsx", "table", difficulty="hard")},
    {"id": "RD-137", "kb_id": KB_ID, "module": "rag",
     "question": "2026H2 商机漏斗里华东区的商机金额、加权金额和预计赢率是多少？",
     "annotation": A("xlsx_sales_pipeline_2026h2", ["xlsx_sales_pipeline_2026h2"],
                     ["3,800万元", "1,420万元", "62%"], "table_value"),
     "metadata": M("xlsx", "table")},
    {"id": "RD-138", "kb_id": KB_ID, "module": "rag",
     "question": "2026-07 云资源账单合计多少万元？",
     "annotation": A("table_cloud_cost_2026", ["table_cloud_cost_2026"], ["86.4万元"],
                     "table_value"),
     "metadata": M("xlsx", "table")},
    {"id": "RD-139", "kb_id": KB_ID, "module": "rag",
     "question": "2026-06 接口调用量、峰值 QPS 和错误率分别是多少？",
     "annotation": A("table_api_calls_2026", ["table_api_calls_2026"],
                     ["42,100万次", "12,800", "0.31%"], "table_value"),
     "metadata": M("xlsx", "table")},
    {"id": "RD-140", "kb_id": KB_ID, "module": "rag",
     "question": "内部课程目录里总计多少门课程？必修几门？",
     "annotation": A("table_training_courses", ["table_training_courses"],
                     ["课程总计46门", "必修8门"], "table_value"),
     "metadata": M("xlsx", "table")},
    {"id": "RD-141", "kb_id": KB_ID, "module": "rag",
     "question": "候选人漏斗里在流程人数合计多少？已发 Offer 多少份？",
     "annotation": A("table_candidate_pipeline", ["table_candidate_pipeline"],
                     ["63人", "17份"], "table_value",
                     perms=("general", "hr_confidential")),
     "metadata": M("xlsx", "table", group="cross_hiring")},

    # ---- CSV：GBK/GB18030 5 份 + UTF-8 无 BOM 1 份 + UTF-8-sig 6 份 ----
    {"id": "RD-142", "kb_id": KB_ID, "module": "rag",
     "question": "供应商联系人名录里 A 类供应商有几家？合计几家？",
     "annotation": A("csv_gbk_supplier_contacts", ["csv_gbk_supplier_contacts"],
                     ["A类4家", "合计6家"], "table_value"),
     "metadata": M("csv", "table", group="cross_procurement")},
    {"id": "RD-143", "kb_id": KB_ID, "module": "rag",
     "question": "仓库库存台账里处于预警状态的 SKU 有几项？",
     "annotation": A("csv_gbk_warehouse_inventory", ["csv_gbk_warehouse_inventory"],
                     ["预警SKU 3项"], "table_value"),
     "metadata": M("csv", "table")},
    {"id": "RD-144", "kb_id": KB_ID, "module": "rag",
     "question": "2025 年渠道销售里代理渠道的销售额是多少？占比多少？",
     "annotation": A("csv_gbk_channel_sales_2025", ["csv_gbk_channel_sales_2025"],
                     ["3,240万元", "48.6%"], "table_value"),
     "metadata": M("csv", "table")},
    {"id": "RD-145", "kb_id": KB_ID, "module": "rag",
     "question": "2026 上半年共办了几场培训？累计参训多少人次？",
     "annotation": A("csv_gbk_hr_training_records", ["csv_gbk_hr_training_records"],
                     ["5场", "242人次"], "table_value",
                     perms=("general", "hr_confidential")),
     "metadata": M("csv", "table", difficulty="medium")},
    {"id": "RD-146", "kb_id": KB_ID, "module": "rag",
     "question": "在售产品目录里旗舰产品是哪个？当前什么版本？",
     "annotation": A("csv_utf8_nobom_product_catalog", ["csv_utf8_nobom_product_catalog"],
                     ["星辰盾", "v3.2"], "table_value"),
     "metadata": M("csv", "table")},
    {"id": "RD-147", "kb_id": KB_ID, "module": "rag",
     "question": "2026-06 月末在职人数是多少？",
     "annotation": A("table_headcount_monthly", ["table_headcount_monthly"], ["512人"],
                     "table_value"),
     "metadata": M("csv", "table")},
    {"id": "RD-148", "kb_id": KB_ID, "module": "rag",
     "question": "2026-06 搜索竞价渠道的投放金额与单线索成本是多少？",
     "annotation": A("table_marketing_spend_2026", ["table_marketing_spend_2026"],
                     ["286万元", "386元"], "table_value"),
     "metadata": M("csv", "table")},
    {"id": "RD-149", "kb_id": KB_ID, "module": "rag",
     "question": "2026 上半年开票金额合计多少？作废几张？",
     "annotation": A("table_invoice_records_2026", ["table_invoice_records_2026"],
                     ["19,200万元", "作废14张"], "table_value",
                     perms=("general", "finance_restricted")),
     "metadata": M("csv", "table", difficulty="medium", group="cross_finance")},
    {"id": "RD-150", "kb_id": KB_ID, "module": "rag",
     "question": "2026-06 的客户 NPS 和满意率是多少？",
     "annotation": A("table_customer_satisfaction_2026",
                     ["table_customer_satisfaction_2026"], ["46", "91.3%"], "table_value"),
     "metadata": M("csv", "table")},
    {"id": "RD-151", "kb_id": KB_ID, "module": "rag",
     "question": "2026 上半年采购订单合计多少单？金额多少？平均交付几天？",
     "annotation": A("table_purchase_orders_2026", ["table_purchase_orders_2026"],
                     ["236单", "3,180万元", "25天"], "table_value"),
     "metadata": M("csv", "table", group="cross_procurement")},
    {"id": "RD-152", "kb_id": KB_ID, "module": "rag",
     "question": "面试评价记录里评分最高的是哪个岗位？多少分？",
     "annotation": A("table_interview_records", ["table_interview_records"],
                     ["算法工程师", "4.6分"], "table_value",
                     perms=("general", "hr_confidential")),
     "metadata": M("csv", "table", difficulty="medium")},
    {"id": "RD-153", "kb_id": KB_ID, "module": "rag",
     "question": "商标与域名台账里注册商标多少件？域名多少个？",
     "annotation": A("legal_trademark_list", ["legal_trademark_list"],
                     ["注册商标23件", "域名17个"], "table_value"),
     "metadata": M("csv", "legal")},

    # ---- 跨文档关联组（§7 跨文档检索）----
    {"id": "RD-154", "kb_id": KB_ID, "module": "rag",
     "question": "公司 2025 年一共发布了多少个版本？当前月活跃用户多少？"
                "网关对单请求体大小的限制是多少？",
     "annotation": A("report_product_annual_2025",
                     ["report_product_annual_2025", "xlsx_product_metrics_2026",
                      "layout_spec_api_gateway"],
                     ["27个版本", "MAU 124,000", "2MB"], "cross_doc"),
     "metadata": M("docx", "report", difficulty="hard", group="cross_product")},
    {"id": "RD-155", "kb_id": KB_ID, "module": "rag",
     "question": "向中科智联采购 GPU 服务器的合同金额是多少？上半年采购订单总额"
                "和平均交付天数是多少？供应商准入要交几项材料？",
     "annotation": A("legal_purchase_contract_zhongke",
                     ["legal_purchase_contract_zhongke", "table_purchase_orders_2026",
                      "sop_vendor_onboarding", "csv_gbk_supplier_contacts"],
                     ["468万元", "3,180万元", "25天", "5项材料"], "cross_doc"),
     "metadata": M("md", "legal", difficulty="hard", group="cross_procurement")},
    {"id": "RD-156", "kb_id": KB_ID, "module": "rag",
     "question": "现行故障 SOP 的 P1 恢复时限是多少？2026Q2 实际发生几起 P1 事件？"
                "平均检测时长多少？",
     "annotation": A("sop_incident_response_v3",
                     ["sop_incident_response_v3", "report_incident_2026q2",
                      "notes_weekly_ops_2026"],
                     ["90分钟内恢复", "P1级1起", "23分钟"], "cross_doc",
                     vreq=VREQ_CURRENT),
     "metadata": M("pdf", "sop", difficulty="hard", group="cross_incident")},
    {"id": "RD-157", "kb_id": KB_ID, "module": "rag",
     "question": "今年招聘 HC 由谁终审？常规岗位要面试几轮？"
                "当前候选人在流程的有多少人？",
     "annotation": A("policy_recruitment",
                     ["policy_recruitment", "sop_interview_hiring",
                      "table_candidate_pipeline"],
                     ["CEO终审", "3轮", "63人"], "cross_doc",
                     perms=("general", "hr_confidential")),
     "metadata": M("md", "policy", difficulty="hard", group="cross_hiring")},
    {"id": "RD-158", "kb_id": KB_ID, "module": "rag",
     "question": "把 2025 年净利润、2026 年净利润预算和 2026 上半年开票金额汇总给我。",
     "annotation": A("layout_report_fin_annual_2025",
                     ["layout_report_fin_annual_2025", "xlsx_finance_budget_2026",
                      "table_invoice_records_2026"],
                     ["6,840万元", "8,100万元", "19,200万元"], "cross_doc",
                     perms=("general", "finance_restricted")),
     "metadata": M("pdf", "report", difficulty="hard", group="cross_finance")},
    {"id": "RD-159", "kb_id": KB_ID, "module": "rag",
     "question": "数据出境评估的结论是什么？DPA 里服务终止后多久删除数据？"
                "公司数据分级分几级？",
     "annotation": A("legal_data_export_assessment",
                     ["legal_data_export_assessment", "legal_dpa_processing",
                      "policy_data_classification"],
                     ["结论为可通过", "90天内删除", "四级"], "cross_doc",
                     perms=("general", "legal_confidential")),
     "metadata": M("pdf", "legal", difficulty="hard", group="cross_data_compliance")},

    # ---- 新增版本链：故障 SOP / 报销办法 / 信息安全制度 ----
    {"id": "RD-160", "kb_id": KB_ID, "module": "rag",
     "question": "故障应急响应 SOP 三个版本里，P1 级故障的修复时限分别是多少？",
     "annotation": A("sop_incident_response_v3",
                     ["sop_incident_response_v1", "sop_incident_response_v2",
                      "sop_incident_response_v3"],
                     ["V1为4小时", "V2为2小时", "V3为90分钟"], "multi_condition",
                     vreq=VREQ_CHAIN_INCIDENT),
     "metadata": M("pdf", "sop", difficulty="hard", group="version_chain")},
    {"id": "RD-161", "kb_id": KB_ID, "module": "rag",
     "question": "2026 年 2 月时执行的费用报销办法，报销时限是多长？",
     "annotation": A("policy_expense_v1", ["policy_expense_v1"], ["60日内"],
                     "multi_condition", vreq=VREQ_AS_OF_202602),
     "metadata": M("md", "policy", difficulty="hard", group="version_chain")},
    {"id": "RD-162", "kb_id": KB_ID, "module": "rag",
     "question": "按现行报销办法，电子发票查重是怎么规定的？",
     "annotation": A("policy_expense_v3", ["policy_expense_v3"],
                     ["系统统一查重", "重复报销直接退回并通报"], "multi_condition",
                     vreq=VREQ_CURRENT),
     "metadata": M("pdf", "policy", difficulty="hard", group="version_chain")},
    {"id": "RD-163", "kb_id": KB_ID, "module": "rag",
     "question": "三个版本的费用报销办法，报销时限各是多少？",
     "annotation": A("policy_expense_v3",
                     ["policy_expense_v1", "policy_expense_v2", "policy_expense_v3"],
                     ["V1为60日", "V2为30日", "V3为20个工作日"], "multi_condition",
                     vreq=VREQ_CHAIN_EXPENSE),
     "metadata": M("pdf", "policy", difficulty="hard", group="version_chain")},
    {"id": "RD-164", "kb_id": KB_ID, "module": "rag",
     "question": "2026 年 3 月时适用的信息安全制度，数据密级分为几级？",
     "annotation": A("policy_infosec", ["policy_infosec"], ["三级"], "multi_condition",
                     vreq=VREQ_AS_OF_202603),
     "metadata": M("md", "policy", difficulty="hard", group="version_chain")},

    # ---- 拒答：无证据 ----
    {"id": "RD-165", "kb_id": KB_ID, "module": "rag",
     "question": "公司 2027 年的营业收入目标是多少？",
     "annotation": A(None, [], [], "no_evidence", refuse=True, reason="no_evidence"),
     "metadata": M("n/a", "n/a", no_answer=True)},
    {"id": "RD-166", "kb_id": KB_ID, "module": "rag",
     "question": "公司与远洋控股签署的战略合作协议金额是多少？",
     "annotation": A(None, [], [], "no_evidence", refuse=True, reason="no_evidence"),
     "metadata": M("n/a", "n/a", no_answer=True)},

    # ---- 拒答：权限不足（general 视角问受限文档）----
    {"id": "RD-167", "kb_id": KB_ID, "module": "rag",
     "question": "公司当前全公司的人员编制缺口是多少？",
     "annotation": A("xlsx_hr_headcount_2026", ["xlsx_hr_headcount_2026"], [],
                     "no_evidence", refuse=True, reason="permission",
                     perms=("general",)),
     "metadata": M("xlsx", "table", difficulty="medium", no_answer=True)},
    {"id": "RD-168", "kb_id": KB_ID, "module": "rag",
     "question": "A1 机房部署了多少个在用节点？",
     "annotation": A("xlsx_ops_capacity_plan", ["xlsx_ops_capacity_plan"], [],
                     "no_evidence", refuse=True, reason="permission",
                     perms=("general",)),
     "metadata": M("xlsx", "table", difficulty="medium", no_answer=True)},
    {"id": "RD-169", "kb_id": KB_ID, "module": "rag",
     "question": "2026 年度合规审查发现的高风险事项具体是哪三项？",
     "annotation": A("legal_compliance_review_2026", ["legal_compliance_review_2026"], [],
                     "no_evidence", refuse=True, reason="permission",
                     perms=("general",)),
     "metadata": M("pdf", "legal", difficulty="medium", no_answer=True)},
]

# ---- 已回填的 expected_chunk_ids（索引后由 runner 实测写回，重生成不得清零）----
# 生成期只做「保住」：A() 里不带 chunk_ids 的用例保持 null，带过的在此回填。
BACKFILLED_CHUNK_IDS: dict[str, tuple[list[str], list[str]]] = {
    "RD-001": (["faq_employee_0"], ["访客 Wi-Fi 名称 XC-Guest"]),
    "RD-002": (["faq_employee_0"], []),
    "RD-003": (["faq_employee_0"], []),
    "RD-004": (["faq_ops_0"], []),
    "RD-005": (["contract_bluewhale_0"], ["合同编号：XC-HT-2025-041"]),
    "RD-006": (["scan_asset_disposal_0"], []),
    "RD-007": (["policy_seal_0", "policy_seal_1", "policy_seal_2", "policy_seal_3",
                 "policy_seal_4", "policy_seal_5", "policy_seal_6", "policy_seal_7"], []),
    "RD-008": (["dict_customer_0"], []),
    "RD-009": (["policy_attendance_rnd_0", "policy_attendance_rnd_1",
                 "policy_attendance_rnd_2", "policy_attendance_rnd_3",
                 "policy_attendance_rnd_4", "policy_attendance_rnd_5",
                 "policy_attendance_rnd_6", "policy_attendance_rnd_7"], []),
    "RD-010": (["policy_attendance_mkt_0", "policy_attendance_mkt_1",
                 "policy_attendance_mkt_2", "policy_attendance_mkt_3",
                 "policy_attendance_mkt_4", "policy_attendance_mkt_5",
                 "policy_attendance_mkt_6", "policy_attendance_mkt_7",
                 "policy_attendance_mkt_8", "policy_attendance_mkt_9"], []),
    "RD-011": (["manual_meetingroom_0", "manual_meetingroom_1", "manual_meetingroom_2",
                 "manual_meetingroom_3", "manual_meetingroom_4", "manual_meetingroom_5",
                 "manual_meetingroom_6", "manual_meetingroom_7"], []),
    "RD-012": (["policy_supplier_0", "policy_supplier_1", "policy_supplier_2",
                 "policy_supplier_3", "policy_supplier_4", "policy_supplier_5",
                 "policy_supplier_6", "policy_supplier_7"], []),
    "RD-013": (["table_pricing_0"], []),
    "RD-014": (["table_hrcost_2026q2_0", "table_hrcost_2026q2_1", "table_hrcost_2026q2_2",
                 "table_hrcost_2026q2_3", "table_hrcost_2026q2_4",
                 "table_hrcost_2026q2_5"], []),
    "RD-015": (["table_servers_0"], []),
    "RD-016": (["table_sales_2026_0"], []),
    "RD-017": (["table_tickets_2026_0"], []),
    "RD-018": (["contract_bluewhale_0", "contract_bluewhale_1", "contract_bluewhale_2",
                 "contract_bluewhale_3", "contract_bluewhale_4", "contract_bluewhale_5",
                 "contract_bluewhale_6", "contract_bluewhale_7", "contract_bluewhale_8",
                 "contract_bluewhale_9", "contract_office_lease_0"], []),
    "RD-019": (["policy_travel_v1_0", "policy_travel_v1_1", "policy_travel_v1_2",
                 "policy_travel_v1_3", "policy_travel_v1_4", "policy_travel_v1_5",
                 "policy_travel_v1_6", "policy_travel_v1_7", "policy_travel_v1_8",
                 "policy_travel_v2_0", "policy_travel_v2_1", "policy_travel_v2_2",
                 "policy_travel_v2_3", "policy_travel_v2_4", "policy_travel_v2_5",
                 "policy_travel_v2_6", "policy_travel_v2_7", "policy_travel_v2_8",
                 "policy_travel_v2_9", "policy_travel_v3_0"], []),
    "RD-020": (["report_fin_h1_2026_0", "table_sales_2026_0"], []),
    "RD-021": (["manual_onboarding_1", "manual_onboarding_5"],
               ["需提前 5 个工作日 在 OA 上预约答辩时间"]),
    "RD-022": (["manual_meetingroom_0", "manual_meetingroom_1", "manual_meetingroom_2",
                 "manual_meetingroom_3", "manual_meetingroom_4", "manual_meetingroom_5",
                 "manual_meetingroom_6", "manual_meetingroom_7"], []),
    "RD-023": (["policy_travel_v2_0", "policy_travel_v2_1", "policy_travel_v2_2",
                 "policy_travel_v2_3", "policy_travel_v2_4", "policy_travel_v2_5",
                 "policy_travel_v2_6", "policy_travel_v2_7", "policy_travel_v2_8",
                 "policy_travel_v2_9"], []),
    "RD-024": (["policy_travel_v1_0", "policy_travel_v1_1", "policy_travel_v1_2",
                 "policy_travel_v1_3", "policy_travel_v1_4", "policy_travel_v1_5",
                 "policy_travel_v1_6", "policy_travel_v1_7", "policy_travel_v1_8"], []),
    "RD-025": (["policy_travel_v3_0"], []),
    "RD-032": (["policy_infosec_0", "policy_infosec_1", "policy_infosec_2",
                 "policy_infosec_3", "policy_infosec_4", "policy_infosec_5",
                 "policy_infosec_6", "policy_infosec_7"], []),
    "RD-033": (["report_cs_2025_0"], []),
    "RD-034": (["report_q2_product_0"], []),
    "RD-035": (["scan_access_request_0"], []),
    "RD-036": (["table_expense_h1_2026_0", "table_expense_h1_2026_1",
                 "table_expense_h1_2026_2", "table_expense_h1_2026_3",
                 "table_expense_h1_2026_4"], []),
}

# ---------------------------------------------------------------- 文档清单

def doc(doc_id, rel, fmt, doctype, text, perms="general", dept=None,
        version=None, scanned=False, group=None, **extra):
    """登记一份语料。

    extra 扩展字段（2026-09-17 扩容）：
      layout   = "complex"：复杂版面（双栏/表格/页眉页脚）/ "multi_page"：多页长文；
      encoding = CSV 写盘编码（默认 utf-8-sig）；sheets = XLSX 工作表数。
      仅用于自校验与形态统计，ingest 侧不消费。
    """
    d = {"doc_id": doc_id, "file": rel, "format": fmt, "doc_type": doctype,
         "kb_id": KB_ID, "permission_scope": perms, "department": dept,
         "version": version, "is_scanned": scanned, "group": group,
         "key_facts": [], "section_anchors": []}
    d.update(extra)
    return d

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

# ---------------------------------------------------------------- 新增版本链 / 关联组

INCIDENT_VERSIONS = {
    "sop_incident_response_v1": {"version_id": "v1", "effective_from": "2024-10-01",
                                 "effective_to": "2025-05-31", "supersedes": None,
                                 "superseded_by": "sop_incident_response_v2"},
    "sop_incident_response_v2": {"version_id": "v2", "effective_from": "2025-06-01",
                                 "effective_to": "2026-08-14",
                                 "supersedes": "sop_incident_response_v1",
                                 "superseded_by": "sop_incident_response_v3"},
    "sop_incident_response_v3": {"version_id": "v3", "effective_from": "2026-08-15",
                                 "effective_to": None,
                                 "supersedes": "sop_incident_response_v2",
                                 "superseded_by": None},
}

EXPENSE_VERSIONS = {
    "policy_expense_v1": {"version_id": "v1", "effective_from": "2024-07-01",
                          "effective_to": "2025-08-31", "supersedes": None,
                          "superseded_by": "policy_expense_v2"},
    "policy_expense_v2": {"version_id": "v2", "effective_from": "2025-09-01",
                          "effective_to": "2026-06-30",
                          "supersedes": "policy_expense_v1",
                          "superseded_by": "policy_expense_v3"},
    "policy_expense_v3": {"version_id": "v3", "effective_from": "2026-07-01",
                          "effective_to": None, "supersedes": "policy_expense_v2",
                          "superseded_by": None},
}

INFOSEC_VERSIONS = {
    # v1 = 探路版既有文档 policy_infosec（生效 2025-01-01），版本元数据在
    # VERSION_PATCH 里附加，不动它的正文与 doc_id。
    "policy_infosec": {"version_id": "v1", "effective_from": "2025-01-01",
                       "effective_to": "2026-06-30", "supersedes": None,
                       "superseded_by": "policy_infosec_v2"},
    "policy_infosec_v2": {"version_id": "v2", "effective_from": "2026-07-01",
                          "effective_to": None, "supersedes": "policy_infosec",
                          "superseded_by": None},
}

VERSION_PATCH = dict(INFOSEC_VERSIONS)

VERSION_CHAINS = {
    "chain_travel": ["policy_travel_v1", "policy_travel_v2", "policy_travel_v3"],
    "chain_incident_sop": ["sop_incident_response_v1", "sop_incident_response_v2",
                           "sop_incident_response_v3"],
    "chain_expense": ["policy_expense_v1", "policy_expense_v2", "policy_expense_v3"],
    "chain_infosec": ["policy_infosec", "policy_infosec_v2"],
}

SIMILAR_PAIRS = {
    "pair_attendance": ["policy_attendance_rnd", "policy_attendance_mkt"],
    "pair_overtime": ["policy_overtime_rnd", "policy_overtime_cs"],
}

# 跨文档关联组：组内文档在真实业务里被同一类问题联合引用（§7 跨文档检索素材）
CROSS_DOC_GROUPS = {
    "cross_procurement": ["legal_purchase_contract_zhongke", "sop_vendor_onboarding",
                          "table_purchase_orders_2026"],
    "cross_incident": ["report_incident_2026q2", "sop_incident_response_v3",
                       "notes_weekly_ops_2026"],
    "cross_hiring": ["policy_recruitment", "sop_interview_hiring",
                     "table_candidate_pipeline"],
    "cross_finance": ["layout_report_fin_annual_2025", "xlsx_finance_budget_2026",
                      "table_invoice_records_2026"],
    "cross_product": ["report_product_annual_2025", "xlsx_product_metrics_2026",
                      "layout_spec_api_gateway"],
    "cross_data_compliance": ["legal_data_export_assessment", "legal_dpa_processing",
                              "policy_data_classification"],
}

# ---------------------------------------------------------------- 新增 73 份文档（27 → 100）

NEW_DOCS = [
    # ---------------- MD（17 份）----------------
    doc("legal_nnn_agreement", "md/legal_双向保密协议_NNN.md", "md", "legal",
        MD_LEGAL_NNN, dept="法务"),
    doc("legal_ip_assignment", "md/legal_知识产权转让协议.md", "md", "legal",
        MD_LEGAL_IP_ASSIGNMENT, dept="法务"),
    doc("legal_purchase_contract_zhongke", "md/legal_设备采购合同_中科智联.md", "md",
        "legal", MD_LEGAL_PURCHASE_ZHONGKE, dept="法务", group="cross_procurement"),
    doc("legal_settlement_memo", "md/legal_争议和解备忘录.md", "md", "legal",
        MD_LEGAL_SETTLEMENT, dept="法务"),
    doc("policy_export_control", "md/policy_出口管制合规管理办法.md", "md", "policy",
        MD_POLICY_EXPORT_CONTROL, dept="法务"),
    doc("policy_recruitment", "md/policy_招聘管理制度.md", "md", "policy",
        MD_POLICY_RECRUITMENT, dept="HR", group="cross_hiring"),
    doc("policy_performance_appraisal", "md/policy_绩效考核管理办法.md", "md", "policy",
        MD_POLICY_PERFORMANCE, dept="HR"),
    doc("policy_software_asset", "md/policy_软件资产管理办法.md", "md", "policy",
        MD_POLICY_SOFTWARE_ASSET, dept="IT"),
    doc("policy_expense_v1", "md/policy_费用报销管理办法_v1.md", "md", "policy",
        MD_POLICY_EXPENSE_V1, dept="财务部",
        version=EXPENSE_VERSIONS["policy_expense_v1"], group="version_chain"),
    doc("policy_overtime_rnd", "md/policy_加班工时管理制度_研发中心.md", "md", "policy",
        MD_POLICY_OVERTIME_RND, dept="HR", group="similar_pair"),
    doc("policy_overtime_cs", "md/policy_加班工时管理制度_客服部.md", "md", "policy",
        MD_POLICY_OVERTIME_CS, dept="HR", group="similar_pair"),
    doc("sop_refund_processing", "md/sop_客户退款处理流程.md", "md", "sop",
        MD_SOP_REFUND, dept="客服部"),
    doc("sop_vendor_onboarding", "md/sop_供应商准入作业流程.md", "md", "sop",
        MD_SOP_VENDOR_ONBOARDING, dept="采购部", group="cross_procurement"),
    doc("sop_incident_response_v1", "md/sop_线上故障应急响应_v1.md", "md", "sop",
        MD_SOP_INCIDENT_V1, dept="IT",
        version=INCIDENT_VERSIONS["sop_incident_response_v1"], group="version_chain"),
    doc("report_incident_2026q2", "md/report_2026Q2安全事件报告.md", "md", "report",
        MD_REPORT_INCIDENT_2026Q2, dept="安全应急中心", group="cross_incident"),
    doc("manual_ops_runbook", "md/manual_运维作业手册.md", "md", "manual",
        MD_MANUAL_OPS_RUNBOOK, dept="IT", layout="multi_page"),
    doc("spec_data_warehouse", "md/spec_数据仓库建设规范.md", "md", "spec",
        MD_SPEC_DATA_WAREHOUSE, dept="数据部", layout="multi_page"),
    doc("policy_infosec_v2", "md/policy_信息安全管理制度_v2.md", "md", "policy",
        MD_POLICY_INFOSEC_V2, dept="数据安全委员会",
        version=INFOSEC_VERSIONS["policy_infosec_v2"], group="version_chain"),

    # ---------------- DOCX（11 份）----------------
    doc("legal_labor_contract_template", "docx/legal_劳动合同模板_2026版.docx", "docx",
        "legal", None, dept="HR"),
    doc("legal_dpa_processing", "docx/legal_数据处理协议_DPA.docx", "docx", "legal",
        None, dept="法务", layout="multi_page", group="cross_data_compliance"),
    doc("legal_case_litigation_2026", "docx/legal_2026年在办诉讼进展.docx", "docx",
        "legal", None, dept="法务", perms="legal_confidential"),
    doc("sop_incident_response_v2", "docx/sop_线上故障应急响应_v2.docx", "docx", "sop",
        None, dept="IT", version=INCIDENT_VERSIONS["sop_incident_response_v2"],
        group="version_chain"),
    doc("sop_release_deploy", "docx/sop_版本发布上线流程.docx", "docx", "sop", None,
        dept="研发中心"),
    doc("sop_backup_recovery_drill", "docx/sop_备份恢复演练流程.docx", "docx", "sop",
        None, dept="IT"),
    doc("sop_interview_hiring", "docx/sop_招聘面试流程.docx", "docx", "sop", None,
        dept="HR", group="cross_hiring"),
    doc("policy_expense_v2", "docx/policy_费用报销管理办法_v2.docx", "docx", "policy",
        None, dept="财务部", version=EXPENSE_VERSIONS["policy_expense_v2"],
        group="version_chain"),
    doc("report_product_annual_2025", "docx/report_2025年度产品总结.docx", "docx",
        "report", None, dept="产品部", layout="multi_page", group="cross_product"),
    doc("report_board_resolution_2026", "docx/report_董事会决议_20260628.docx", "docx",
        "report", None, dept="董事会办公室"),
    doc("faq_finance_reimbursement", "docx/faq_财务报销常见问题.docx", "docx", "faq",
        None, dept="财务部"),

    # ---------------- PDF（15 份：文本 6 / 复杂版面 7 / 扫描件 2）----------------
    doc("legal_software_license", "pdf/legal_软件许可协议.pdf", "pdf", "legal", None,
        dept="法务"),
    doc("sop_data_destruction", "pdf/sop_数据销毁流程.pdf", "pdf", "sop", None,
        dept="数据安全委员会"),
    doc("policy_expense_v3", "pdf/policy_费用报销管理办法_v3.pdf", "pdf", "policy", None,
        dept="财务部", version=EXPENSE_VERSIONS["policy_expense_v3"],
        group="version_chain"),
    doc("policy_remote_work", "pdf/policy_远程办公管理办法.pdf", "pdf", "policy", None,
        dept="HR"),
    doc("report_performance_test_2026", "pdf/report_压测报告_2026Q2.pdf", "pdf", "report",
        None, dept="研发中心"),
    doc("contract_annual_maintenance", "pdf/contract_年度运维服务合同.pdf", "pdf",
        "contract", None, dept="IT", perms="finance_restricted"),

    doc("layout_report_fin_annual_2025", "pdf/report_2025年度财务报告.pdf", "pdf",
        "report", None, dept="财务部", perms="finance_restricted",
        layout="complex", group="cross_finance"),
    doc("layout_manual_employee_handbook", "pdf/manual_员工手册_2026版.pdf", "pdf",
        "manual", None, dept="HR", layout="complex"),
    doc("layout_spec_api_gateway", "pdf/spec_API网关技术规范.pdf", "pdf", "spec", None,
        dept="研发中心", layout="complex", group="cross_product"),
    doc("layout_tender_evaluation", "pdf/legal_评标办法.pdf", "pdf", "legal", None,
        dept="法务", layout="complex"),
    doc("legal_compliance_review_2026", "pdf/legal_2026年度合规审查报告.pdf", "pdf",
        "legal", None, dept="法务", perms="legal_confidential", layout="complex"),
    doc("legal_data_export_assessment", "pdf/legal_数据出境自评估报告.pdf", "pdf",
        "legal", None, dept="法务", perms="legal_confidential", layout="complex",
        group="cross_data_compliance"),
    doc("sop_incident_response_v3", "pdf/sop_线上故障应急响应_v3.pdf", "pdf", "sop", None,
        dept="IT", version=INCIDENT_VERSIONS["sop_incident_response_v3"],
        layout="complex", group="version_chain"),

    doc("scan_expense_claim_form", "pdf/scan_费用报销单.pdf", "pdf", "form", None,
        dept="财务部", scanned=True, group="scanned"),
    doc("scan_training_signin", "pdf/scan_培训签到表.pdf", "pdf", "form", None,
        dept="HR", scanned=True, group="scanned"),

    # ---------------- TXT（8 份）----------------
    doc("notes_weekly_ops_2026", "txt/notes_运维周会纪要_20260907.txt", "txt", "notes",
        TXT_NOTES_WEEKLY_OPS, dept="IT", group="cross_incident"),
    doc("notes_strategy_retreat_2026", "txt/notes_战略务虚会纪要_2026.txt", "txt",
        "notes", TXT_NOTES_STRATEGY_RETREAT, dept="总裁办", layout="multi_page"),
    doc("faq_it_helpdesk", "txt/faq_IT帮助中心FAQ.txt", "txt", "faq", TXT_FAQ_IT_HELPDESK,
        dept="IT"),
    doc("dict_order_domain", "txt/readme_数据字典_订单域.txt", "txt", "reference",
        TXT_DICT_ORDER_DOMAIN, dept="数据部"),
    doc("sop_access_review", "txt/sop_权限季度复核.txt", "txt", "sop", TXT_SOP_ACCESS_REVIEW,
        dept="IT"),
    doc("sop_change_management", "txt/sop_变更管理.txt", "txt", "sop",
        TXT_SOP_CHANGE_MANAGEMENT, dept="研发中心"),
    doc("policy_data_classification", "txt/policy_数据分类分级标准.txt", "txt", "policy",
        TXT_POLICY_DATA_CLASSIFICATION, dept="数据安全委员会",
        group="cross_data_compliance"),

    # ---------------- XLSX（10 份：多 Sheet 6 + 单 Sheet 4）----------------
    doc("xlsx_finance_budget_2026", "xlsx/table_2026年度预算表.xlsx", "xlsx", "table",
        None, dept="财务部", perms="finance_restricted", sheets=4,
        group="cross_finance"),
    doc("xlsx_hr_headcount_2026", "xlsx/table_2026人力编制台账.xlsx", "xlsx", "table",
        None, dept="HR", perms="hr_confidential", sheets=3),
    doc("xlsx_ops_capacity_plan", "xlsx/table_容量规划表.xlsx", "xlsx", "table", None,
        dept="IT", perms="it_admin", sheets=3),
    doc("xlsx_product_metrics_2026", "xlsx/table_产品核心指标.xlsx", "xlsx", "table",
        None, dept="产品部", sheets=3, group="cross_product"),
    doc("xlsx_legal_contract_register", "xlsx/table_合同台账.xlsx", "xlsx", "table",
        None, dept="法务", perms="legal_confidential", sheets=3),
    doc("xlsx_sales_pipeline_2026h2", "xlsx/table_2026H2商机漏斗.xlsx", "xlsx", "table",
        None, dept="销售部", sheets=3),
    doc("table_cloud_cost_2026", "xlsx/table_云资源账单_2026.xlsx", "xlsx", "table",
        None, dept="财务部"),
    doc("table_api_calls_2026", "xlsx/table_接口调用量_2026.xlsx", "xlsx", "table",
        None, dept="研发中心"),
    doc("table_training_courses", "xlsx/table_内部课程目录.xlsx", "xlsx", "table", None,
        dept="HR"),
    doc("table_candidate_pipeline", "xlsx/table_候选人漏斗.xlsx", "xlsx", "table", None,
        dept="HR", perms="hr_confidential", group="cross_hiring"),

    # ---------------- CSV（12 份：GBK/GB18030 4 + UTF-8 无 BOM 1 + UTF-8-sig 7）----------------
    doc("csv_gbk_supplier_contacts", "csv/table_供应商联系人名录_GBK.csv", "csv", "table",
        None, dept="采购部", encoding="gbk", group="cross_procurement"),
    doc("csv_gbk_warehouse_inventory", "csv/table_仓库库存台账_GBK.csv", "csv", "table",
        None, dept="仓储部", encoding="gbk"),
    doc("csv_gbk_channel_sales_2025", "csv/table_渠道销售_2025_GB18030.csv", "csv",
        "table", None, dept="销售部", encoding="gb18030"),
    doc("csv_gbk_hr_training_records", "csv/table_培训记录_GBK.csv", "csv", "table",
        None, dept="HR", perms="hr_confidential", encoding="gbk"),
    doc("csv_utf8_nobom_product_catalog", "csv/table_在售产品目录_UTF8无BOM.csv", "csv",
        "table", None, dept="产品部", encoding="utf-8"),
    doc("table_headcount_monthly", "csv/table_月度在职人数.csv", "csv", "table", None,
        dept="HR"),
    doc("table_marketing_spend_2026", "csv/table_市场投放费用_2026.csv", "csv", "table",
        None, dept="市场部"),
    doc("table_invoice_records_2026", "csv/table_发票台账_2026.csv", "csv", "table",
        None, dept="财务部", perms="finance_restricted", group="cross_finance"),
    doc("table_customer_satisfaction_2026", "csv/table_客户满意度调查_2026.csv", "csv",
        "table", None, dept="客服部"),
    doc("table_purchase_orders_2026", "csv/table_采购订单台账_2026.csv", "csv", "table",
        None, dept="采购部", group="cross_procurement"),
    doc("table_interview_records", "csv/table_面试评价记录.csv", "csv", "table", None,
        dept="HR", perms="hr_confidential"),
    doc("legal_trademark_list", "csv/legal_商标与域名台账.csv", "csv", "legal", None,
        dept="法务"),
]

DOCS.extend(NEW_DOCS)

# VERSION_PATCH：把版本元数据补挂到探路版既有文档上（正文与 doc_id 一律不动）。
for _d in DOCS:
    _v = VERSION_PATCH.get(_d["doc_id"])
    if _v and not _d["version"]:
        _d["version"] = _v
del _d, _v

# ---------------------------------------------------------------- 生成器实现

def write_text(rel: str, content: str, encoding: str = "utf-8") -> None:
    p = FILES_DIR / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding=encoding, newline="\n")


def write_csv(rel: str, header: list, rows: list, encoding: str) -> None:
    """按指定中文编码写 CSV（模拟不同来源系统导出的编码习惯）。

    utf-8-sig：Excel 导出的常见形态（含 BOM）；utf-8：Linux 侧脚本产出（无 BOM）；
    gbk / gb18030：老 ERP 与 Windows 业务系统导出的常见形态。
    CsvParser 的读取顺序是 utf-8-sig → gbk，因此 gbk/gb18030 走回退分支。
    """
    p = FILES_DIR / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    # gbk 系列：先校验字符可编码，避免生成下游一定解不开的乱码文件
    if encoding in ("gbk", "gb18030"):
        for row in [header] + list(rows):
            for c in row:
                str(c).encode(encoding)
    with p.open("w", encoding=encoding, newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


# 纯文本（MD / TXT）注册表：相对路径 → 内容。键必须覆盖 DOCS 中全部 md/txt。
MD_TXT_FILES: dict[str, str] = {
    # ---- 探路版 12 份（逐字保留）----
    "md/policy_考勤管理制度_研发中心.md": MD_ATTENDANCE_RND,
    "md/policy_考勤管理制度_市场部.md": MD_ATTENDANCE_MKT,
    "md/policy_印章使用管理办法.md": MD_SEAL,
    "md/faq_员工常见问题.md": MD_FAQ_EMPLOYEE,
    "md/manual_新员工入职手册.md": MD_ONBOARDING,
    "md/policy_信息安全管理制度.md": MD_INFOSEC,
    "md/contract_蓝鲸数据服务合同.md": MD_CONTRACT_BLUEWHALE,
    "md/report_客服运营年度报告_2025.md": MD_REPORT_CS,
    "md/policy_差旅费管理办法_v1.md": MD_TRAVEL_V1,
    "txt/faq_运维值班FAQ.txt": TXT_FAQ_OPS,
    "txt/notes_产品评审会议纪要_20260830.txt": TXT_NOTES_REVIEW,
    "txt/readme_数据字典_客户域.txt": TXT_DICT_CUSTOMER,
    # ---- 扩容新增 25 份 ----
    "md/legal_双向保密协议_NNN.md": MD_LEGAL_NNN,
    "md/legal_知识产权转让协议.md": MD_LEGAL_IP_ASSIGNMENT,
    "md/legal_设备采购合同_中科智联.md": MD_LEGAL_PURCHASE_ZHONGKE,
    "md/legal_争议和解备忘录.md": MD_LEGAL_SETTLEMENT,
    "md/policy_出口管制合规管理办法.md": MD_POLICY_EXPORT_CONTROL,
    "md/policy_招聘管理制度.md": MD_POLICY_RECRUITMENT,
    "md/policy_绩效考核管理办法.md": MD_POLICY_PERFORMANCE,
    "md/policy_软件资产管理办法.md": MD_POLICY_SOFTWARE_ASSET,
    "md/policy_费用报销管理办法_v1.md": MD_POLICY_EXPENSE_V1,
    "md/policy_加班工时管理制度_研发中心.md": MD_POLICY_OVERTIME_RND,
    "md/policy_加班工时管理制度_客服部.md": MD_POLICY_OVERTIME_CS,
    "md/sop_客户退款处理流程.md": MD_SOP_REFUND,
    "md/sop_供应商准入作业流程.md": MD_SOP_VENDOR_ONBOARDING,
    "md/sop_线上故障应急响应_v1.md": MD_SOP_INCIDENT_V1,
    "md/report_2026Q2安全事件报告.md": MD_REPORT_INCIDENT_2026Q2,
    "md/manual_运维作业手册.md": MD_MANUAL_OPS_RUNBOOK,
    "md/spec_数据仓库建设规范.md": MD_SPEC_DATA_WAREHOUSE,
    "txt/notes_运维周会纪要_20260907.txt": TXT_NOTES_WEEKLY_OPS,
    "txt/notes_战略务虚会纪要_2026.txt": TXT_NOTES_STRATEGY_RETREAT,
    "txt/faq_IT帮助中心FAQ.txt": TXT_FAQ_IT_HELPDESK,
    "txt/readme_数据字典_订单域.txt": TXT_DICT_ORDER_DOMAIN,
    "md/policy_信息安全管理制度_v2.md": MD_POLICY_INFOSEC_V2,
    "txt/sop_权限季度复核.txt": TXT_SOP_ACCESS_REVIEW,
    "txt/sop_变更管理.txt": TXT_SOP_CHANGE_MANAGEMENT,
    "txt/policy_数据分类分级标准.txt": TXT_POLICY_DATA_CLASSIFICATION,
}


def gen_md_txt() -> None:
    n = 0
    for d in DOCS:
        if d["format"] not in ("md", "txt"):
            continue
        if d["file"] not in MD_TXT_FILES:
            raise KeyError(f"[MISSING_GENERATOR] {d['doc_id']} 缺 MD/TXT 内容: {d['file']}")
        write_text(d["file"], MD_TXT_FILES[d["file"]])
        n += 1
    print(f"    MD/TXT {n} 份")


# DOCX 构建器注册表：doc_id → builder(Document)。
def _docx_grid(d, rows):
    table = d.add_table(rows=len(rows), cols=len(rows[0]))
    table.style = "Table Grid"
    for i, row in enumerate(rows):
        for j, val in enumerate(row):
            table.rows[i].cells[j].text = str(val)
    return table


def _docx_travel_v2(d: Document) -> None:
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


def _docx_q2_product(d: Document) -> None:
    d.add_heading("Q2 产品迭代总结（2026）", level=1)
    d.add_paragraph("星盾 2.0 于 2026-04-18 正式上线，灰度期 3 周，覆盖 40% 客户。")
    d.add_paragraph("本季度累计修复 P1 级缺陷 12 个，P2 级缺陷 37 个。")
    d.add_paragraph("下一季度重点：会议助手语音转写能力。")


def _docx_meetingroom(d: Document) -> None:
    d.add_heading("会议室使用规范", level=1)
    d.add_heading("1. 预订", level=2)
    d.add_paragraph("会议室通过「星辰办公」App 预订，单次最长 3 小时。")
    d.add_paragraph("如需取消，请至少提前 30 分钟释放，避免资源浪费。")
    d.add_heading("2. 设备", level=2)
    d.add_paragraph("投影仪 HDMI 转接头在 B1 层前台借阅，用后当日归还。")
    d.add_heading("3. 卫生", level=2)
    d.add_paragraph("会后请自行清理桌面，白板内容保留不得超过 24 小时。")


def _docx_supplier(d: Document) -> None:
    d.add_heading("供应商准入管理办法", level=1)
    d.add_paragraph("供应商准入实行百分制评分，综合评分达到 80 分及以上方可准入。")
    d.add_paragraph("供应商年审每年 3 月统一开展，年审不合格的暂停合作资格。")
    d.add_paragraph("列入黑名单的供应商，3 年内不得再次申请准入。")
    _docx_grid(d, [("维度", "权重", "说明"),
                   ("资质合规", "30%", "营业执照、行业资质"),
                   ("交付能力", "40%", "产能、历史履约记录"),
                   ("价格竞争力", "30%", "报价与成本合理性")])


# ---------------- 扩容新增 DOCX ----------------

def _docx_labor_contract(d: Document) -> None:
    d.add_heading("劳动合同模板（2026 版）", level=1)
    d.add_paragraph("使用说明：本模板由法务部维护，适用于正式员工，劳务派遣不适用。")
    d.add_heading("第一条 合同期限", level=2)
    d.add_paragraph("首次签订劳动合同期限为 3 年，其中试用期 6 个月。")
    d.add_heading("第二条 工作内容与地点", level=2)
    d.add_paragraph("工作地点为福州市鼓楼区；因经营需要调整工作地点的，双方另行协商。")
    d.add_heading("第三条 劳动报酬", level=2)
    d.add_paragraph("工资构成以录用通知书为准，工资支付日为次月 15 日。")
    d.add_heading("第四条 竞业限制", level=2)
    d.add_paragraph("竞业限制期限不超过 2 年，自劳动合同解除或终止之日起计算。")
    d.add_paragraph("竞业限制补偿金按离职前十二个月平均工资的 30% 按月支付。")
    d.add_heading("第五条 违约责任", level=2)
    d.add_paragraph("员工违反竞业限制约定的，须返还已领取的全部补偿金并支付违约金。")
    _docx_grid(d, [("条款", "内容", "备注"),
                   ("合同期限", "3 年", "含试用期 6 个月"),
                   ("竞业限制", "最长 2 年", "补偿按月平均工资 30%"),
                   ("支付日", "次月 15 日", "遇节假日顺延")])


def _docx_dpa(d: Document) -> None:
    d.add_heading("数据处理协议（DPA）", level=1)
    d.add_paragraph("协议编号：XC-DPA-2026-002")
    d.add_paragraph("控制者（甲方）：签约客户    处理者（乙方）：%s" % COMPANY)
    d.add_paragraph("签署日期：2026-03-02    生效日期：2026-04-01")
    d.add_heading("1. 角色与适用", level=2)
    d.add_paragraph("甲方为数据控制者，乙方为受托处理者；乙方仅在甲方书面指示范围内处理个人数据。")
    d.add_heading("2. 处理目的与场景", level=2)
    d.add_paragraph("处理目的限定为三类：客户画像服务、风控模型运行、客服工单支撑。")
    d.add_paragraph("超出上述目的的用途须另行签署书面补充协议。")
    d.add_heading("3. 数据类别", level=2)
    _docx_grid(d, [("数据类别", "示例字段", "处理方式"),
                   ("身份信息", "姓名、手机号（脱敏）", "加密存储"),
                   ("交易信息", "订单金额、购买频次", "聚合分析"),
                   ("设备信息", "设备型号、系统版本", "匿名化处理")])
    d.add_heading("4. 子处理者", level=2)
    d.add_paragraph("乙方使用子处理者须提前 15 个工作日书面告知甲方，甲方有权提出异议。")
    d.add_paragraph("子处理者清单由乙方在其官网维护，变更须同步通知并要求同等保护水平。")
    d.add_heading("5. 跨境传输", level=2)
    d.add_paragraph("未经完成评估的国家或地区不得跨境传输；已列入白名单的地区方可传输。")
    d.add_paragraph("跨境传输前须完成个人信息保护影响评估并留存记录 3 年。")
    d.add_heading("6. 留存与删除", level=2)
    d.add_paragraph("服务终止后乙方须在 90 天内完成数据删除，并出具删除证明。")
    d.add_heading("7. 安全措施", level=2)
    d.add_paragraph("乙方采取传输加密、最小权限访问、全量操作留痕三项基础措施。")
    d.add_paragraph("安全事件须在 24 小时内通知甲方，并配合完成监管报告。")
    d.add_heading("8. 审计权", level=2)
    d.add_paragraph("甲方享有年度审计权，每年可行使 1 次，须提前 30 日书面通知乙方。")
    d.add_paragraph("审计费用由甲方承担，但发现重大不符项的由乙方承担。")
    d.add_heading("9. 期限与终止", level=2)
    d.add_paragraph("本协议与主服务协议同期限；主协议终止的，本协议自动终止。")


def _docx_litigation(d: Document) -> None:
    d.add_heading("2026 年在办诉讼案件进展（机密）", level=1)
    d.add_paragraph("密级：restricted    编制部门：法务部    编制日期：2026-08-20")
    d.add_paragraph("截至 2026-08-20，公司在办诉讼与仲裁案件共 3 起。")
    d.add_heading("1. 案件概览", level=2)
    _docx_grid(d, [("案由", "标的额", "阶段", "预计结案"),
                   ("技术服务合同纠纷（对方：蓝鲸数据）", "1,180 万元", "一审已开庭", "2026 年 Q4"),
                   ("买卖合同纠纷", "96 万元", "庭前调解", "2026 年 Q3"),
                   ("劳动争议", "18 万元", "仲裁审理中", "2026 年 Q4")])
    d.add_heading("2. 重大案件说明", level=2)
    d.add_paragraph("与蓝鲸数据的技术服务合同纠纷标的额最高，为一审开庭后的实体审理阶段，"
                    "开庭日期为 2026-04-22，预计 2026 年第四季度作出一审判决。")
    d.add_heading("3. 财务影响", level=2)
    d.add_paragraph("本季度未新增预计负债；若一审不利，最大风险敞口约为标的额的六成。")


def _docx_incident_v2(d: Document) -> None:
    d.add_heading("线上故障应急响应 SOP（V2）", level=1)
    d.add_paragraph("编号：XC-SOP-IT-2025-011    发布日期：2025-05-22    生效日期：2025-06-01")
    d.add_paragraph("本版本自 2025-06-01 起施行，同时废止 V1 版（XC-SOP-IT-2024-006）。")
    d.add_heading("1. 故障分级", level=2)
    d.add_paragraph("P1 级：服务整体不可用；P2 级：核心功能受损；P3 级：一般体验问题。")
    d.add_heading("2. 修复时限", level=2)
    d.add_paragraph("P1 级故障须在 2 小时内恢复服务，P2 级 8 小时内，P3 级 2 个工作日内。")
    d.add_heading("3. 通报机制（本次新增）", level=2)
    d.add_paragraph("通报渠道升级为邮件 + 短信 + 企业微信三通道，首次通报不超过 15 分钟。")
    d.add_paragraph("P1 级故障须建立应急指挥群，30 分钟内到位并完成分工。")
    d.add_heading("4. 复盘", level=2)
    d.add_paragraph("P1、P2 级故障须在恢复后 3 个工作日内提交复盘报告并跟踪改进项闭环。")


def _docx_release_deploy(d: Document) -> None:
    d.add_heading("版本发布上线流程 SOP", level=1)
    d.add_paragraph("编号：XC-SOP-RD-2026-004    适用：研发中心各产品线")
    d.add_heading("1. 发布窗口", level=2)
    d.add_paragraph("常规发布窗口为每周二、周四 20:00 至 24:00，非窗口发布须特批。")
    d.add_heading("2. 灰度策略", level=2)
    d.add_paragraph("灰度比例按 5% → 20% → 100% 三阶段推进，每阶段观察不少于 30 分钟。")
    d.add_heading("3. 回滚条件", level=2)
    d.add_paragraph("出现 P1、P2 级缺陷，或核心接口错误率超过 1% 时立即回滚。")
    d.add_paragraph("回滚须在决策后 15 分钟内执行完毕，执行结果同步至发布群。")
    d.add_heading("4. 发布后验证", level=2)
    d.add_paragraph("全量发布后须完成冒烟用例集并留存验证截图 30 天。")


def _docx_backup_drill(d: Document) -> None:
    d.add_heading("备份恢复演练流程 SOP", level=1)
    d.add_paragraph("编号：XC-SOP-IT-2025-023    适用：IT 运维部")
    d.add_heading("1. 演练周期", level=2)
    d.add_paragraph("每半年开展一次全链路备份恢复演练，上半年安排在 5 月，下半年安排在 11 月。")
    d.add_heading("2. 目标指标", level=2)
    d.add_paragraph("恢复时间目标 RTO 为 4 小时，恢复点目标 RPO 为 15 分钟。")
    d.add_heading("3. 最近一次演练", level=2)
    d.add_paragraph("最近一次演练日期为 2026-05-20，实际恢复耗时 2 小时 48 分，达到 RTO 要求。")
    d.add_paragraph("下次演练计划安排在 2026 年 11 月中旬，演练范围增加对象存储。")
    d.add_heading("4. 演练产出", level=2)
    d.add_paragraph("演练须出具《恢复演练记录》，含耗时明细、差异点与改进项，报分管副总裁。")


def _docx_interview_process(d: Document) -> None:
    d.add_heading("招聘面试流程 SOP", level=1)
    d.add_paragraph("编号：XC-SOP-HR-2025-009    适用：HR 招聘组与用人部门")
    d.add_heading("1. 面试轮次", level=2)
    d.add_paragraph("常规岗位实行 3 轮面试：技术面、主管面、HRBP 面；总监级另加一轮高管面。")
    d.add_heading("2. 面评时效", level=2)
    d.add_paragraph("每轮面试结束后评价须在 24 小时内提交，逾期系统自动提醒并不予安排下一轮。")
    d.add_heading("3. 录用审批", level=2)
    d.add_paragraph("录用审批由区域总监终审，通过后发放 Offer，Offer 有效期为 7 天。")
    d.add_heading("4. 面试官纪律", level=2)
    d.add_paragraph("不得询问与岗位无关的婚育计划、家庭财产等信息，违者取消面试官资格。")


def _docx_expense_v2(d: Document) -> None:
    d.add_heading("费用报销管理办法（V2）", level=1)
    d.add_paragraph("制度编号：XC-CW-2025-033    发布日期：2025-08-20    生效日期：2025-09-01")
    d.add_paragraph("本版本自 2025-09-01 起施行，同时废止 V1 版（XC-CW-2024-021）。")
    d.add_heading("1. 审批层级", level=2)
    d.add_paragraph("维持两级审批：部门负责人 → 财务部；单笔超过 5 万元的加签财务总监。")
    d.add_heading("2. 报销时限", level=2)
    d.add_paragraph("费用发生后须在 30 日内提交报销，超期须附书面说明并经分管副总裁批准。")
    d.add_heading("3. 发票管理（本次新增）", level=2)
    d.add_paragraph("电子发票须通过系统查重，同一发票重复报销的一律退回并通报。")
    d.add_heading("4. 差额处理", level=2)
    d.add_paragraph("实报金额低于原借款的，须在报销时同步提交还款说明。")


def _docx_product_annual(d: Document) -> None:
    d.add_heading("2025 年度产品总结报告", level=1)
    d.add_paragraph("编制部门：产品部    发布日期：2026-01-22")
    d.add_heading("1. 交付概况", level=2)
    d.add_paragraph("全年累计发布版本 27 个，其中大版本 4 个，线上重大回滚 0 次。")
    d.add_heading("2. 客户与活跃度", level=2)
    d.add_paragraph("年末付费客户数达到 1,842 家，年度活跃租户 3,120 个。")
    d.add_paragraph("客户净推荐值 NPS 为 46，较上一年度提升 7 分。")
    d.add_heading("3. 重点交付", level=2)
    d.add_paragraph("数据接入平台 1.0 于 2025-09-12 发布，支撑了 11 个新客户的自助接入。")
    d.add_paragraph("移动端工作台完成重构，启动耗时从 3.4 秒降至 1.2 秒。")
    d.add_heading("4. 质量与缺陷", level=2)
    d.add_paragraph("全年生产环境 P1 级缺陷 9 个，平均修复时长 2 小时 36 分。")
    _docx_grid(d, [("指标", "2024", "2025", "同比"),
                   ("付费客户数", "1,506", "1,842", "+22.3%"),
                   ("NPS", "39", "46", "+7"),
                   ("活跃租户", "2,410", "3,120", "+29.5%"),
                   ("P1 级缺陷", "15", "9", "-40.0%")])
    d.add_heading("5. 2026 展望", level=2)
    d.add_paragraph("重点推进语音转写能力与跨租户模板市场，目标新增付费客户 600 家。")


def _docx_board_resolution(d: Document) -> None:
    d.add_heading("第三届董事会第七次会议决议", level=1)
    d.add_paragraph("会议日期：2026-06-28    会议地点：福州    董事长主持")
    d.add_heading("决议一", level=2)
    d.add_paragraph("批准公司 2026 年度资本性支出预算，总额为人民币 3,200 万元。")
    d.add_heading("决议二", level=2)
    d.add_paragraph("批准设立新加坡全资子公司，首期注册资本为 200 万新元。")
    d.add_heading("决议三", level=2)
    d.add_paragraph("授权管理层办理境外投资备案、公司注册登记等相关手续，授权有效期 12 个月。")
    d.add_heading("表决情况", level=2)
    _docx_grid(d, [("议案", "同意", "反对", "弃权", "结果"),
                   ("资本性支出预算", "7", "0", "0", "通过"),
                   ("设立新加坡子公司", "6", "1", "0", "通过"),
                   ("授权管理层", "7", "0", "0", "通过")])


def _docx_faq_finance(d: Document) -> None:
    d.add_heading("财务报销常见问题（财务版）", level=1)
    d.add_heading("1. 发票抬头开错了怎么办？", level=2)
    d.add_paragraph("抬头或税号错误的发票不予受理，须联系开票方作废后重开。")
    d.add_heading("2. 差旅报销需要哪些附件？", level=2)
    d.add_paragraph("须提供出差申请单、行程单（机票/火车票）与对应发票，三者信息须一致。")
    d.add_heading("3. 大额报销有什么额外要求？", level=2)
    d.add_paragraph("单笔金额 1 万元以上的，须附合同或采购审批说明，否则财务部不予受理。")
    d.add_heading("4. 外币如何折算？", level=2)
    d.add_paragraph("按费用发生当日中国人民银行公布的中间汇率折算，汇率截图须一并上传。")
    d.add_heading("5. 报销被退回怎么查询？", level=2)
    d.add_paragraph("在报销系统内查看退回原因，补充材料后可再次提交，同一单可重提 3 次。")


DOCX_BUILDERS: dict[str, object] = {
    # ---- 探路版 4 份（逐字保留）----
    "policy_travel_v2": _docx_travel_v2,
    "report_q2_product": _docx_q2_product,
    "manual_meetingroom": _docx_meetingroom,
    "policy_supplier": _docx_supplier,
    # ---- 扩容新增 11 份 ----
    "legal_labor_contract_template": _docx_labor_contract,
    "legal_dpa_processing": _docx_dpa,
    "legal_case_litigation_2026": _docx_litigation,
    "sop_incident_response_v2": _docx_incident_v2,
    "sop_release_deploy": _docx_release_deploy,
    "sop_backup_recovery_drill": _docx_backup_drill,
    "sop_interview_hiring": _docx_interview_process,
    "policy_expense_v2": _docx_expense_v2,
    "report_product_annual_2025": _docx_product_annual,
    "report_board_resolution_2026": _docx_board_resolution,
    "faq_finance_reimbursement": _docx_faq_finance,
}


def gen_docx() -> None:
    from docx import Document

    n = 0
    for d in DOCS:
        if d["format"] != "docx":
            continue
        build = DOCX_BUILDERS.get(d["doc_id"])
        if build is None:
            raise KeyError(f"[MISSING_GENERATOR] {d['doc_id']} 缺 DOCX 构建器")
        p = FILES_DIR / d["file"]
        p.parent.mkdir(parents=True, exist_ok=True)
        docx_obj = Document()
        build(docx_obj)
        docx_obj.save(str(p))
        n += 1
    print(f"    DOCX {n} 份")


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


# 单栏文本 PDF 内容注册表：doc_id → (标题, 行列表)。
PDF_TEXT_PAGES: dict[str, tuple[str, list[str]]] = {
    # ---- 探路版 3 份（逐字保留）----
    "policy_travel_v3": ("差旅费管理办法 V3", [
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
    ]),
    "contract_office_lease": ("办公室租赁合同", [
        "办公室租赁合同",
        "出租方（甲方）：恒基地产发展有限公司",
        "承租方（乙方）：星辰云科技有限公司",
        "",
        "一、租赁标的：闽江大道 88 号星辰大厦 12~15 层。",
        "二、租期：2025 年 1 月 1 日至 2027 年 12 月 31 日。",
        "三、租金：月租金人民币 185,000 元（18.5 万元），含物业费。",
        "四、支付方式：押三付一，押金为 3 个月租金。",
        "五、违约：任一方提前解约须提前 90 日书面通知并支付 2 个月租金违约金。",
    ]),
    "report_fin_h1_2026": ("2026上半年财务摘要", [
        "2026 年上半年财务摘要（机密）",
        "密级：confidential    发布：财务部    日期：2026-07-15",
        "",
        "一、营业收入：上半年营业收入人民币 234,000,000 元（2.34 亿元），",
        "    同比增长 18.6%。",
        "二、毛利率：61.2%。",
        "三、经营性现金流：人民币 3,180 万元。",
        "四、费用：研发费用 6,820 万元，销售费用 4,310 万元。",
    ]),
    # ---- 扩容新增 6 份 ----
    "legal_software_license": ("软件许可协议（星辰盾企业版）", [
        "软件许可协议（星辰盾企业版）",
        "协议编号：XC-LIC-2026-001    许可方：星辰云科技有限公司",
        "",
        "一、授权席位：本次授权 500 个命名用户席位。",
        "二、许可期限：3 年，自 2026-01-01 至 2028-12-31。",
        "三、许可费用：年许可费人民币 96 万元，按年预付。",
        "四、禁止转授权：被许可方不得向第三方转让、转授权或用于托管服务。",
        "五、超席位使用：超出授权席位的须补签增补协议并补缴差额。",
    ]),
    "sop_data_destruction": ("数据销毁流程 SOP", [
        "数据销毁流程 SOP",
        "编号：XC-SOP-DS-2025-017    适用：各部门与数据中心",
        "",
        "一、销毁方式：纸质文件物理粉碎，电子介质逻辑覆写 3 次后消磁。",
        "二、审批层级：部门负责人 + 数据安全官双签后方可执行。",
        "三、销毁记录：含销毁清单、执行人与监督人，保存 5 年。",
        "四、外包要求：委托外部销毁的须签署保密协议并全程录像留存。",
        "五、例外：涉及未结争议的数据暂缓销毁，由法务书面确认后处置。",
    ]),
    "policy_expense_v3": ("费用报销管理办法 V3", [
        "费用报销管理办法（V3）",
        "制度编号：XC-CW-2026-008    发布日期：2026-06-18",
        "生效日期：2026-07-01，同时废止 V2 版（XC-CW-2025-033）。",
        "",
        "一、报销时限：费用发生后须在 20 个工作日内提交报销单。",
        "二、凭证：单笔金额达到 5,000 元的须附合同或验收说明。",
        "三、发票查重：电子发票统一由系统查重，重复报销直接退回并通报。",
        "四、审批层级：部门负责人、财务部两级；超过 5 万元加签财务总监。",
        "五、支付：每月 15 日与月末两次集中支付，节假日顺延。",
    ]),
    "policy_remote_work": ("远程办公管理办法", [
        "远程办公管理办法",
        "制度编号：XC-HR-2026-012    发布部门：人力资源部    生效日期：2026-03-01",
        "",
        "一、办公天数：每人每月远程办公不超过 6 天，不得连月累计使用。",
        "二、审批：须提前 1 个工作日在 OA 系统提交申请并获得主管批准。",
        "三、在线要求：核心时段 10:00 至 16:00 必须在线并保持即时响应。",
        "四、安全要求：远程访问内网一律通过 VPN，禁止使用公共网络处理机密数据。",
        "五、例外：怀孕七个月以上或因病行动不便的员工，经 HR 核准可放宽天数限制。",
    ]),
    "report_performance_test_2026": ("2026 年 Q2 全链路压测报告", [
        "2026 年 Q2 全链路压测报告",
        "测试日期：2026-06-21    执行部门：研发中心质量工程组",
        "",
        "一、测试目标：验证大促场景下的下单链路容量，目标 TPS 3,000。",
        "二、实测结果：峰值 TPS 达到 3,860，超出目标 28.7%。",
        "三、响应指标：下单接口 P99 延迟 218 毫秒，平均延迟 96 毫秒。",
        "四、资源水位：应用节点 CPU 峰值 78%，数据库连接池占用 64%。",
        "五、结论：容量冗余充足，建议将限流阈值上调至 4,200 TPS。",
    ]),
    "contract_annual_maintenance": ("年度运维服务合同", [
        "年度运维服务合同",
        "合同编号：XC-HT-2026-027",
        "委托方：星辰云科技有限公司    服务方：维智科技有限公司",
        "",
        "一、服务对象：核心业务平台（含网关、订单、结算三个子系统）。",
        "二、服务期：2026 年 1 月 1 日至 2026 年 12 月 31 日。",
        "三、服务费用：年度服务费人民币 128 万元，按季支付。",
        "四、响应要求：提供 7x24 支持，P1 级故障 15 分钟内响应，2 小时内恢复。",
        "五、违约：未达响应时限的，每次按月度服务费的 3% 扣减。",
    ]),
}

# 扫描件（图版 PDF）内容注册表：doc_id → 行列表。
PDF_SCAN_PAGES: dict[str, list[str]] = {
    # ---- 探路版 2 份（逐字保留）----
    "scan_asset_disposal": [
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
    ],
    "scan_access_request": [
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
    ],
    # ---- 扩容新增 2 份 ----
    "scan_expense_claim_form": [
        "星辰云科技有限公司  费用报销单",
        "",
        "报销单号：BX-2026-0518",
        "申请人：苏黎（产品部）    报销日期：2026-08-03",
        "费用事由：杭州客户现场拜访，含高铁往返与住宿 2 晚",
        "报销金额：人民币 3,480.00 元",
        "附件票据：4 张（高铁票 2 张、住宿发票 1 张、市内交通 1 张）",
        "",
        "审批记录：",
        "  部门负责人：同意（2026-08-05）",
        "  财务部：审核通过（2026-08-09）——审批完成日期 2026-08-09",
    ],
    "scan_training_signin": [
        "星辰云科技有限公司  培训签到表",
        "",
        "培训主题：数据安全合规宣贯（2026 年第 3 期）",
        "培训日期：2026-06-25    地点：星辰大厦 3 楼培训室",
        "讲师：李洪（安全应急中心）",
        "应到人数：48 人    实到人数：45 人    请假 3 人",
        "课时：2 学时    考核方式：现场闭卷（合格线 80 分）",
    ],
}


def gen_pdf_text() -> None:
    n = 0
    for d in DOCS:
        if d["format"] != "pdf" or d["doc_id"] not in PDF_TEXT_PAGES:
            continue
        title, lines = PDF_TEXT_PAGES[d["doc_id"]]
        _text_pdf(FILES_DIR / d["file"], title, lines)
        n += 1
    print(f"    文本 PDF {n} 份")


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


# ---------------------------------------------------------------- 复杂版面 PDF 引擎

def _layout_styles() -> dict:
    """复杂版面通用样式（CJK 折行走 reportlab 的 'CJK' wordWrap）。"""
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle

    base = dict(fontName="SimHei", wordWrap="CJK")
    return {
        "cover_title": ParagraphStyle("ct", fontSize=22, leading=34, alignment=1,
                                      textColor=colors.HexColor("#12355b"), **base),
        "cover_sub": ParagraphStyle("cs", fontSize=12, leading=22, alignment=1,
                                    textColor=colors.HexColor("#555555"), **base),
        "h1": ParagraphStyle("h1", fontSize=14, leading=21, spaceBefore=10, spaceAfter=6,
                             textColor=colors.HexColor("#12355b"), **base),
        "h2": ParagraphStyle("h2", fontSize=11.5, leading=18, spaceBefore=7, spaceAfter=3,
                             **base),
        "body": ParagraphStyle("b", fontSize=10, leading=17, spaceAfter=5, **base),
        "small": ParagraphStyle("s", fontSize=9, leading=14, spaceAfter=3,
                                textColor=colors.HexColor("#555555"), **base),
        "cell": ParagraphStyle("c", fontSize=8.5, leading=12, **base),
    }


def _layout_table(rows: list, cell_style, widths=None):
    from reportlab.lib import colors
    from reportlab.platypus import Paragraph, Table, TableStyle

    data = [[Paragraph(f"<b>{c}</b>" if i == 0 else str(c), cell_style)
             for c in row] for i, row in enumerate(rows)]
    t = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#9aa5b1")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dce5f0")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return t


class _Layout:
    """复杂版面排版的流式封装（封面单栏 / 正文双栏 / 表格整页单栏混合）。"""

    def __init__(self, story: list, styles: dict, col_w: float):
        self.story, self.S, self.col_w = story, styles, col_w

    # 文本
    def title(self, t): self.story.append(_P(t, self.S["cover_title"]))
    def line(self, t): self.story.append(_P(t, self.S["cover_sub"]))
    def h1(self, t): self.story.append(_P(t, self.S["h1"]))
    def h2(self, t): self.story.append(_P(t, self.S["h2"]))
    def p(self, t): self.story.append(_P(t, self.S["body"]))
    def small(self, t): self.story.append(_P(t, self.S["small"]))
    def gap(self, h=10): self.story.append(_S(1, h))

    def table(self, rows, widths=None, caption=None):
        if caption:
            self.story.append(_P(caption, self.S["small"]))
        self.story.append(_layout_table(rows, self.S["cell"], widths))

    # 版面切换：调用后会另起一页并按目标栏数排版
    def to_two_columns(self):
        self.story.append(_NPT("two"))
        self.story.append(_PB())

    def to_single(self):
        self.story.append(_NPT("single"))
        self.story.append(_PB())


def _P(text, style):
    from reportlab.platypus import Paragraph
    return Paragraph(text, style)


def _S(w, h):
    from reportlab.platypus import Spacer
    return Spacer(w, h)


def _PB():
    from reportlab.platypus import PageBreak
    return PageBreak()


def _NPT(tid):
    from reportlab.platypus import NextPageTemplate
    return NextPageTemplate(tid)


def _layout_pdf(path: Path, title: str, header: str, builder, start: str = "single") -> None:
    """生成复杂版面 PDF：页眉页脚 + 页码 + 单/双栏混合 + 表格。

    start="single"：首页为整页封面；"two"：首页即双栏正文。
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import BaseDocTemplate, Frame, PageTemplate

    pdfmetrics.registerFont(TTFont("SimHei", FONT_HEI))
    W, H = A4
    left = right = 20 * mm
    top, bottom = 22 * mm, 18 * mm
    width = W - left - right
    gutter = 8 * mm
    col_w = (width - gutter) / 2
    body_h = H - top - bottom
    frame_full = Frame(left, bottom, width, body_h, id="full", showBoundary=0)
    frame_l = Frame(left, bottom, col_w, body_h, id="col_l", showBoundary=0)
    frame_r = Frame(left + col_w + gutter, bottom, col_w, body_h, id="col_r",
                    showBoundary=0)

    def _decorate(canvas, doc_ref):
        canvas.saveState()
        canvas.setFont("SimHei", 8)
        canvas.setFillColor(colors.HexColor("#666666"))
        canvas.drawString(left, H - 13 * mm, header)
        canvas.drawRightString(W - right, H - 13 * mm, COMPANY)
        canvas.setStrokeColor(colors.HexColor("#cccccc"))
        canvas.line(left, H - 15 * mm, W - right, H - 15 * mm)
        canvas.line(left, bottom - 4 * mm, W - right, bottom - 4 * mm)
        canvas.drawString(left, bottom - 9 * mm, f"第 {doc_ref.page} 页")
        canvas.restoreState()

    path.parent.mkdir(parents=True, exist_ok=True)
    doc_tpl = BaseDocTemplate(str(path), pagesize=A4, title=title, author=COMPANY,
                              leftMargin=left, rightMargin=right,
                              topMargin=top, bottomMargin=bottom)
    t_single = PageTemplate(id="single", frames=[frame_full], onPage=_decorate)
    t_two = PageTemplate(id="two", frames=[frame_l, frame_r], onPage=_decorate)
    doc_tpl.addPageTemplates([t_single if start == "single" else t_two,
                              t_two if start == "single" else t_single])
    story: list = []
    builder(_Layout(story, _layout_styles(), col_w))
    doc_tpl.build(story)


# ---------------- 复杂版面 PDF 构建器 ----------------

def _pdf_fin_annual(L: _Layout) -> None:
    L.gap(60)
    L.title("星辰云科技有限公司")
    L.gap(24)
    L.line("2025 年度财务报告")
    L.gap(10)
    L.line("密级：restricted（限财务部与经营管理层）")
    L.line("编制部门：财务部")
    L.line("报告日期：2026-03-18")
    L.to_two_columns()
    L.h1("一、经营概况")
    L.p("2025 年公司实现营业收入人民币 4.12 亿元，实现净利润 6,840 万元。")
    L.p("毛利率 58.4%，较上年下降 1.2 个百分点，主要因交付侧人力成本上升。")
    L.p("研发投入 1.02 亿元，占营业收入的 24.8%，连续三年保持在 20% 以上。")
    L.p("销售费用 7,320 万元，管理费用 4,180 万元，两项合计占营业收入 27.9%。")
    L.p("截至 2025 年 12 月 31 日，公司在职员工 478 人，其中研发人员 214 人。")
    L.h1("二、资产负债与现金流")
    L.p("期末总资产 6.42 亿元，总负债 2.48 亿元，资产负债率为 38.7%。")
    L.p("账面货币资金 2.86 亿元，报告期内无短期借款。")
    L.p("全年经营活动产生的现金流量净额为 9,120 万元。")
    L.to_single()
    L.h1("三、合并利润表（简表）")
    L.table([("科目", "2025 年", "占营业收入比"),
             ("营业收入", "41,200 万元", "100.0%"),
             ("营业成本", "17,140 万元", "41.6%"),
             ("研发费用", "10,200 万元", "24.8%"),
             ("净利润", "6,840 万元", "16.6%")],
            caption="表 3-1 合并利润表主要科目（2025 年度）")
    L.to_two_columns()
    L.h1("四、分部经营情况")
    L.p("数据服务分部全年收入 2.36 亿元，占比 57.3%，为公司最主要收入来源。")
    L.p("企业应用分部收入 1.18 亿元，占比 28.6%，同比增速最快。")
    L.p("技术服务分部收入 5,800 万元，占比 14.1%，毛利率低于其他分部。")
    L.to_single()
    L.table([("分部", "收入（万元）", "占比", "同比"),
             ("数据服务", "23,600", "57.3%", "+18.4%"),
             ("企业应用", "11,800", "28.6%", "+31.2%"),
             ("技术服务", "5,800", "14.1%", "+12.6%")],
            caption="表 4-1 分部收入结构")
    L.to_two_columns()
    L.h1("五、风险提示与期后事项")
    L.p("主要客户集中度较上年上升，前五大客户收入占比由 31.2% 升至 38.5%。")
    L.p("应收账款账龄结构上移，一年以上账龄占比达到 6.4%，需加强回款管理。")
    L.p("期后事项：2026 年 1 月完成对某数据标注团队的整体收购，交易金额 2,100 万元。")
    L.h1("六、审计意见")
    L.p("外部审计机构出具标准无保留意见的审计报告，审计签署日期为 2026-03-12。")


def _pdf_handbook(L: _Layout) -> None:
    L.gap(60)
    L.title("员工手册")
    L.gap(20)
    L.line("2026 版（v6.2）")
    L.line("编制部门：人力资源部")
    L.line("施行日期：2026-01-01    修订日期：2025-12-15")
    L.to_two_columns()
    L.h1("第一章 入职与试用")
    L.p("入职当日须提交身份证、学历学位证书、离职证明与体检报告，材料一式一份。")
    L.p("试用期 6 个月，考核由直属上级与 HRBP 共同评定，评定结果在试用期届满前两周告知。")
    L.h1("第二章 工作时间与考勤")
    L.p("公司实行弹性打卡，各部门按属地的弹性区间执行，日标准工时 8 小时。")
    L.p("加班须提前在 OA 提交审批，未审批的加班不计入工时与报酬。")
    L.p("病假须于当日上午 10:00 前在系统报备，连续 3 天以上的须提交医院证明。")
    L.h1("第三章 薪酬与福利")
    L.p("工资支付日为次月 15 日，遇节假日提前至最近工作日发放。")
    L.p("通讯补贴按每人每月 150 元发放，随工资计税。")
    L.p("因公加班至 21:30 以后离岗的，可报销当次返程交通费用。")
    L.p("年度体检统一安排在每年 9 月至 10 月，具体批次由 HR 通知各部门。")
    L.h1("第四章 学习与发展")
    L.p("内部图书馆每次借阅不超过 3 本，借期 30 天，逾期未还暂停借阅权限。")
    L.p("每人每年须完成不少于 40 学时的学习任务，其中合规类课程为必修。")
    L.h1("第五章 行为规范")
    L.p("办公区域禁止吸烟，午休时间请保持公共区域内安静。")
    L.p("未经许可不得拍摄内部屏幕与文件；对外分享材料须经上级确认是否可外发。")
    L.h1("第六章 信息安全")
    L.p("内部及以上密级数据对外导出须经数据保护官书面审批，审批记录留存 3 年。")
    L.p("离开工位须锁屏，涉密纸质文件须入柜存放，不得留置桌面过夜。")
    L.h1("第七章 安全与应急")
    L.p("消防疏散集合点为大厦南广场，疏散演练每半年组织一次。")
    L.p("发生人身伤害事件的，第一时间联系行政值班电话并取得医疗救助。")
    L.h1("第八章 奖惩")
    L.p("奖惩分为通报表扬、专项奖金、通报批评、记过与解除劳动合同五类。")
    L.p("累计两次书面警告的，第三次可直接解除劳动合同。")
    L.h1("第九章 离职")
    L.p("试用期内离职须提前 3 日通知；正式员工须提前 30 日书面通知。")
    L.p("离职当日须完成资产归还、权限回收与工作交接，交接清单由部门负责人签署确认。")


def _pdf_api_gateway(L: _Layout) -> None:
    L.h1("API 网关技术规范")
    L.small("文档版本：v3.2    发布部门：研发中心    生效日期：2026-05-06")
    L.p("本规范对外统一接入层的行为作出约定，所有业务服务接入网关前须满足本规范。")
    L.h1("1. 总体约束")
    L.p("单请求体上限 2MB，超限请求由网关直接返回错误，不转发上游。")
    L.p("默认超时时间为 3 秒，业务确需延长的须在接入时声明，最长不超过 10 秒。")
    L.p("网关集群版本为 v3.2，旧版本 v2.x 已于 2026-04-30 停止维护。")
    L.h1("2. 限流")
    L.p("限流维度为租户 + 接口，默认配额为每租户 500 QPS。")
    L.p("突发流量允许在配额基础上超发 20%，超过部分返回标准限流错误码。")
    L.to_single()
    L.table([("配置项", "默认值", "说明"),
             ("限流维度", "租户 + 接口", "默认配额 500 QPS/租户"),
             ("请求体上限", "2MB", "超限直接拒绝"),
             ("超时时间", "3 秒", "可声明延长至 10 秒"),
             ("重试次数", "2 次", "指数退避，首次间隔 200ms"),
             ("最大页大小", "200 条", "cursor 分页")],
            caption="表 2-1 网关核心参数默认值")
    L.to_two_columns()
    L.h1("3. 鉴权")
    L.p("同时支持 JWT 与 AK/SK 两种鉴权模式，同一接口不得混用。")
    L.p("JWT 令牌有效期 2 小时，刷新令牌有效期 7 天。")
    L.p("AK/SK 模式下签名有效期为 5 分钟，签名失败连续 5 次触发账号临时锁定。")
    L.h1("4. 灰度与追踪")
    L.p("灰度标为 X-Canary，取值为 true 时路由至灰度集群，默认走正式集群。")
    L.p("链路标识为 X-Request-Id，服务端原样回显用于故障排查定位。")
    L.p("幂等键 Idempotency-Key 由调用方生成，服务端保留结果 24 小时。")
    L.h1("5. 错误处理")
    L.p("网关错误码统一为五位数，前两位代表错误类别，如 40 表示调用方错误。")
    L.p("上游不可用时网关返回服务暂不可用错误，并在响应头携带重试建议时间。")


def _pdf_tender(L: _Layout) -> None:
    L.h1("评标办法")
    L.small("文件编号：XC-TD-2026-002    发布部门：法务部    适用：公司自行采购项目")
    L.p("本办法适用于单次采购预算 100 万元以上的项目，其下项目可参照执行。")
    L.h1("第一条 评标原则")
    L.p("评标遵循公平、公正、科学、择优原则，任何单位和个人不得干预评标过程。")
    L.h1("第二条 评标委员会")
    L.p("评标委员会由 5 名成员组成，其中外部专家不得少于 2 名。")
    L.p("评审期间评委不得与投标人对实质性内容进行谈判。")
    L.h1("第三条 评分权重")
    L.p("评审采用综合评分法：技术方案 45%、商务报价 35%、履约能力 20%。")
    L.p("报价得分以有效报价的最低价为基准，最低价得满分，其余按比例折算。")
    L.to_single()
    L.table([("评审维度", "权重", "主要评审要点"),
             ("技术方案", "45%", "需求理解、架构合理性、实施路径"),
             ("商务报价", "35%", "总价、付款条件、报价明细合理性"),
             ("履约能力", "20%", "资质、案例、团队配置、售后承诺")],
            caption="表 3-1 综合评分权重表")
    L.to_two_columns()
    L.h1("第四条 废标情形")
    L.p("投标文件未按招标文件要求密封、加盖公章的作废标处理。")
    L.p("投标报价超过采购预算上限，或报价明显低于成本且无法说明的作废标处理。")
    L.p("有效投标人不足 3 家的，本项目流标并重新组织招标。")
    L.h1("第五条 定标")
    L.p("评标委员会按得分高低推荐 1 至 3 名中标候选人并排序，报采购决策会确定中标人。")
    L.p("评标报告与评分原始记录由法务部归档，保存期限 5 年。")


def _pdf_compliance_review(L: _Layout) -> None:
    L.gap(50)
    L.title("2026 年度合规审查报告")
    L.gap(18)
    L.line("密级：restricted    委托机构：外部法律服务机构")
    L.line("对口部门：法务部    出具日期：2026-09-01")
    L.to_two_columns()
    L.h1("一、审查范围与方法")
    L.p("本次审查覆盖公司 8 条业务线，采用文件审查、抽样核查与访谈相结合的方式。")
    L.p("共抽样凭证 240 份，访谈关键岗位人员 26 人，审查期间为 2026 年 6 月至 8 月。")
    L.h1("二、整体结论")
    L.p("公司合规体系整体运行有效，未发现系统性重大违规事项。")
    L.p("共发现高风险事项 3 项、中风险事项 7 项，低风险事项 15 项。")
    L.h1("三、高风险事项")
    L.p("风险一：两类敏感数据处理缺少书面授权依据，需补做个人信息影响评估。")
    L.p("风险二：个别合同的验收条款与付款条款不匹配，可能造成付款争议。")
    L.p("风险三：一处境外数据同步未按既有流程完成出境评估报备。")
    L.to_single()
    L.table([("风险等级", "数量", "整改要求", "整改截止日"),
             ("高", "3", "专项复盘并出具整改报告", "2026-11-30"),
             ("中", "7", "限期整改并报法务复核", "2026-12-31"),
             ("低", "15", "纳入次年常规优化", "2027-06-30")],
            caption="表 3-1 风险事项处置要求")
    L.to_two_columns()
    L.h1("四、整改安排")
    L.p("全部高风险事项须在 2026-11-30 前完成整改并向管理层书面报告。")
    L.p("未按期完成的事项由法务部逐周跟踪，直至闭环或获得书面豁免。")
    L.h1("五、后续建议")
    L.p("建议将合规审查由年度改为半年度，并在重大业务调整时触发专项审查。")


def _pdf_data_export(L: _Layout) -> None:
    L.gap(50)
    L.title("数据出境自评估报告")
    L.gap(18)
    L.line("密级：restricted    编制部门：法务部")
    L.line("评估完成日期：2026-05-26    报告版本号：v2.0")
    L.to_two_columns()
    L.h1("一、评估背景")
    L.p("因海外客户服务需要，公司拟向境外主体提供少量客户运营数据。")
    L.h1("二、出境基本要素")
    L.p("接收方为公司新加坡子公司，出境目的为海外客户服务与技术支撑。")
    L.p("拟出境数据规模为每年 12.4 万条，主要为已脱敏的客户运营统计数据。")
    L.p("出境方式为加密专线传输，传输链路与存储均使用公司统一密钥托管。")
    L.h1("三、评估结论")
    L.p("经评估，本次数据出境风险可被接受，结论为可通过。")
    L.p("通过的前提是完成个人信息出境标准合同备案，备案后方可实际执行。")
    L.p("标准合同备案日期为 2026-05-26，本次评估结论有效期为 2 年。")
    L.to_single()
    L.table([("评估要素", "结论", "备注"),
             ("出境数量", "12.4 万条/年", "以脱敏运营数据为主"),
             ("接收方", "新加坡子公司", "受公司统一安全策略约束"),
             ("出境目的", "海外客户服务", "不得用于二次营销"),
             ("评估结论", "可通过", "须完成标准合同备案"),
             ("有效期", "2 年", "2026-05-26 起算")],
            caption="表 3-1 数据出境要素摘要")
    L.to_two_columns()
    L.h1("四、约束条件")
    L.p("出境数据不得包含未脱敏的个人信息，也不得再向第三方转提供。")
    L.p("发生接收方所在地法律环境重大变化的，须重新开展出境评估。")


def _pdf_incident_v3(L: _Layout) -> None:
    L.h1("线上故障应急响应 SOP（V3）")
    L.small("编号：XC-SOP-IT-2026-003    生效日期：2026-08-15")
    L.p("本版本自 2026-08-15 起施行，同时废止 V2 版（XC-SOP-IT-2025-011）。")
    L.h1("1. 适用范围")
    L.p("适用于所有面向客户的生产环境服务，含公有云服务与私有化部署托管服务。")
    L.h1("2. 故障分级")
    L.p("P1 级为服务整体不可用或核心数据不可访问，P2 级为核心功能受损。")
    L.p("P3 级为一般体验类问题，不影响主流程使用。")
    L.h1("3. 响应时限（本次收紧）")
    L.p("P1 级故障须在 90 分钟内恢复服务，首次通报不超过 15 分钟。")
    L.p("P2 级故障 6 小时内恢复，P3 级 1 个工作日内给出处理结论。")
    L.h1("4. 值班替补机制（本次新增）")
    L.p("主值无法在 10 分钟内响应的，自动升级至替补值班人，替补须 15 分钟内接手。")
    L.p("替补名单按周更新并在值班群公示，未公示的替补不生效。")
    L.to_single()
    L.table([("环节", "负责人", "参与方", "时限"),
             ("发现与通报", "值班工程师", "监控平台", "15 分钟内"),
             ("应急指挥", "值班经理", "相关业务负责人", "30 分钟内到位"),
             ("止血处置", "主值 / 替补", "研发、运维", "90 分钟内恢复"),
             ("复盘与改进", "主值", "质量工程组", "3 个工作日内")],
            caption="表 4-1 故障处置 RACI 与时限表")
    L.to_two_columns()
    L.h1("5. 通报模板")
    L.p("通报须包含五个要素：影响范围、当前状态、已采取措施、预计恢复时间、负责人。")
    L.p("恢复后 30 分钟内须发布恢复通报，并与首次通报形成闭环。")
    L.h1("6. 复盘要求")
    L.p("P1、P2 级故障须在 3 个工作日内提交复盘报告，改进项须指定责任人与截止日期。")
    L.p("同一根因重复发生两次以上的，升级为专项治理课题。")


PDF_LAYOUT_BUILDERS: dict[str, tuple] = {
    # doc_id → (页眉标题, builder, 起始版面)（复杂版面：双栏 + 表格 + 页眉页脚页码）
    "layout_report_fin_annual_2025": ("2025 年度财务报告（restricted）",
                                      _pdf_fin_annual, "single"),
    "layout_manual_employee_handbook": ("员工手册 2026 版", _pdf_handbook, "single"),
    "layout_spec_api_gateway": ("API 网关技术规范 v3.2", _pdf_api_gateway, "single"),
    "layout_tender_evaluation": ("评标办法 XC-TD-2026-002", _pdf_tender, "single"),
    "legal_compliance_review_2026": ("2026 年度合规审查报告（restricted）",
                                     _pdf_compliance_review, "single"),
    "legal_data_export_assessment": ("数据出境自评估报告（restricted）",
                                     _pdf_data_export, "single"),
    "sop_incident_response_v3": ("线上故障应急响应 SOP V3", _pdf_incident_v3, "single"),
}


def gen_pdf_layout() -> None:
    n = 0
    for d in DOCS:
        spec = PDF_LAYOUT_BUILDERS.get(d["doc_id"])
        if spec is None:
            continue
        header, builder, start = spec
        _layout_pdf(FILES_DIR / d["file"], header, header, builder, start=start)
        n += 1
    print(f"    复杂版面 PDF {n} 份（双栏 + 表格 + 页眉页脚页码）")


def gen_pdf_scan() -> None:
    n = 0
    for d in DOCS:
        if d["format"] != "pdf" or d["doc_id"] not in PDF_SCAN_PAGES:
            continue
        _scan_pdf(FILES_DIR / d["file"], PDF_SCAN_PAGES[d["doc_id"]])
        n += 1
    print(f"    扫描件 PDF {n} 份（文字渲染成图，需 OCR）")


# ---------------------------------------------------------------- XLSX 构建器

def _xlsx_sheet(wb, title: str, rows: list, first: bool = False):
    """写一个工作表：首行为加粗表头。first=True 时写入默认活动表。"""
    import openpyxl

    ws = wb.active if first else wb.create_sheet()
    ws.title = title
    for row in rows:
        ws.append(list(row))
    for cell in ws[1]:
        cell.font = openpyxl.styles.Font(bold=True)
    return ws


# ---- 探路版 3 份（逐字保留）----
def _build_hrcost(wb) -> None:
    _xlsx_sheet(wb, "Q2人力成本", [
        ("部门", "2026Q2人力成本（万元）", "同比"),
        ("研发部", 486.5, "+12.4%"),
        ("市场部", 152.3, "-3.1%"),
        ("客服部", 98.7, "+5.6%"),
        ("行政部", 61.2, "+0.8%"),
        ("财务部", 55.4, "-1.2%"),
    ], first=True)


def _build_pricing(wb) -> None:
    _xlsx_sheet(wb, "定价", [
        ("版本", "年费（元/年）", "包含席位"),
        ("基础版", 999, 5),
        ("专业版", 3999, 20),
        ("企业版", 12999, "不限"),
    ], first=True)


def _build_servers(wb) -> None:
    _xlsx_sheet(wb, "资产清单", [
        ("资产编号", "型号", "机房", "上架日期"),
        ("SL-0012", "华为 2288H V5", "B2机房", "2024-05-11"),
        ("SL-0031", "浪潮 NF5280M6", "A1机房", "2024-11-03"),
        ("SL-0058", "戴尔 R750", "B2机房", "2025-03-19"),
    ], first=True)


# ---- 扩容新增：多 Sheet 6 份 ----
def _build_budget(wb) -> None:
    _xlsx_sheet(wb, "汇总", [
        ("指标", "2026 预算", "2025 实际", "同比"),
        ("营业收入", "52,000 万元", "41,200 万元", "+26.2%"),
        ("成本费用合计", "38,600 万元", "31,570 万元", "+22.3%"),
        ("人力成本", "12,400 万元", "9,860 万元", "+25.8%"),
        ("净利润", "8,100 万元", "6,840 万元", "+18.4%"),
    ], first=True)
    _xlsx_sheet(wb, "收入预算", [
        ("产品线", "收入预算（万元）", "占比"),
        ("数据服务", 28600, "55.0%"),
        ("企业应用", 17400, "33.5%"),
        ("技术服务", 6000, "11.5%"),
    ])
    _xlsx_sheet(wb, "成本费用", [
        ("科目", "预算（万元）", "说明"),
        ("人力成本", 12400, "含社保与公积金"),
        ("外部采购", 9800, "硬件与第三方服务"),
        ("云服务", 6300, "含测试环境"),
        ("市场费用", 4700, "含品牌与投放"),
        ("管理费用", 5400, "含办公租赁"),
    ])
    _xlsx_sheet(wb, "人力编制", [
        ("序列", "编制数", "现有数", "缺口"),
        ("研发", 232, 214, 18),
        ("销售", 148, 131, 17),
        ("交付", 112, 96, 16),
        ("职能", 68, 71, -3),
    ])


def _build_headcount(wb) -> None:
    _xlsx_sheet(wb, "编制现状", [
        ("部门", "编制数", "在职数", "缺口"),
        ("研发中心", 232, 214, 18),
        ("销售部", 148, 131, 17),
        ("交付服务部", 112, 96, 16),
        ("职能中心", 68, 71, -3),
        ("合计", 560, 512, 48),
    ], first=True)
    _xlsx_sheet(wb, "招聘进度", [
        ("序列", "需求人数", "已发 Offer", "已到岗"),
        ("研发", 18, 7, 4),
        ("销售", 17, 6, 5),
        ("交付", 16, 4, 3),
        ("职能", 0, 0, 0),
    ])
    _xlsx_sheet(wb, "离职分析", [
        ("季度", "离职人数", "离职率", "主要流向"),
        ("2026Q1", 7, "1.4%", "同行业厂商"),
        ("2026Q2", 6, "1.2%", "异地发展"),
        ("2026Q3", 5, "1.0%", "继续深造"),
    ])


def _build_capacity(wb) -> None:
    _xlsx_sheet(wb, "资源池", [
        ("机房", "节点数", "可用冗余", "承担业务"),
        ("A1 机房", 46, "28%", "核心交易"),
        ("B2 机房", 38, "21%", "分析与离线"),
        ("托管机房", 24, "35%", "灾备"),
    ], first=True)
    _xlsx_sheet(wb, "容量水位", [
        ("资源", "平均水位", "峰值水位", "告警阈值"),
        ("CPU", "63%", "78%", "≤70%"),
        ("内存", "58%", "71%", "≤75%"),
        ("存储", "66%", "74%", "≤80%"),
        ("带宽", "41%", "62%", "≤70%"),
    ])
    _xlsx_sheet(wb, "扩容计划", [
        ("批次", "计划节点数", "计划时间", "预算（万元）"),
        ("第一批", 12, "2026-10", 620),
        ("第二批", 12, "2026-12", 610),
        ("合计", 24, "2026 下半年", 1230),
    ])


def _build_product_metrics(wb) -> None:
    _xlsx_sheet(wb, "活跃", [
        ("月份", "MAU", "DAU", "付费租户"),
        ("2026-04", "108,000", "21,300", "2,940"),
        ("2026-05", "116,000", "22,800", "3,010"),
        ("2026-06", "124,000", "24,600", "3,120"),
    ], first=True)
    _xlsx_sheet(wb, "留存", [
        ("月份", "次日留存", "月留存", "流失租户"),
        ("2026-04", "82.4%", "65.1%", "38"),
        ("2026-05", "84.1%", "66.8%", "31"),
        ("2026-06", "85.2%", "68.2%", "27"),
    ])
    _xlsx_sheet(wb, "收入贡献", [
        ("产品版本", "收入占比", "ARPU（元/月）"),
        ("企业版", "41.0%", "8,600"),
        ("专业版", "37.2%", "3,200"),
        ("基础版", "21.8%", "980"),
    ])


def _build_contract_register(wb) -> None:
    _xlsx_sheet(wb, "合同台账", [
        ("合同编号", "对方主体", "金额（万元）", "状态"),
        ("XC-HT-2025-041", "蓝鲸数据（厦门）", 120, "履行中"),
        ("XC-CG-2026-014", "中科智联（武汉）", 468, "履行中"),
        ("XC-HT-2026-027", "维智科技", 128, "履行中"),
        ("汇总", "在册合同 187 份", "", "高风险 3 份"),
    ], first=True)
    _xlsx_sheet(wb, "履约节点", [
        ("合同编号", "节点", "计划日期", "状态"),
        ("XC-CG-2026-014", "第二批设备到货", "2026-10-15", "未开始"),
        ("XC-CG-2026-014", "终验", "2026-11-30", "未开始"),
        ("XC-HT-2026-027", "年度服务评价", "2026-12-20", "未开始"),
        ("汇总", "待履约交付节点共 14 个", "", ""),
    ])
    _xlsx_sheet(wb, "风险清单", [
        ("编号", "风险描述", "等级", "责任人"),
        ("R-01", "设备到货进度存在延期风险", "高", "陈斌"),
        ("R-02", "服务级指标口径双方理解不一致", "高", "王倩"),
        ("R-03", "验收单据未按时出具", "高", "苏黎"),
        ("汇总", "高风险合同 3 份", "", ""),
    ])


def _build_pipeline(wb) -> None:
    _xlsx_sheet(wb, "商机汇总", [
        ("区域", "商机金额（万元）", "加权金额（万元）", "预计赢率"),
        ("华东", 3800, 1420, "62%"),
        ("华南", 2900, 1080, "60%"),
        ("华北", 1900, 740, "65%"),
        ("合计", 8600, 3240, "62%"),
    ], first=True)
    _xlsx_sheet(wb, "漏斗阶段", [
        ("阶段", "商机数", "金额（万元）"),
        ("线索", 86, 4200),
        ("方案验证", 31, 2600),
        ("商务谈判", 14, 1300),
        ("即将签约", 6, 500),
    ])
    _xlsx_sheet(wb, "重点客户", [
        ("客户", "产品", "金额（万元）", "预计签约月"),
        ("海联集团", "企业版", 620, "2026-10"),
        ("明远科技", "专业版", 340, "2026-11"),
        ("泰和保险", "企业版", 480, "2026-12"),
    ])


# ---- 扩容新增：单 Sheet 4 份 ----
def _build_cloud_cost(wb) -> None:
    _xlsx_sheet(wb, "云账单", [
        ("月份", "计算（万元）", "存储（万元）", "网络（万元）", "合计（万元）"),
        ("2026-05", 41.2, 12.6, 8.4, 62.2),
        ("2026-06", 45.8, 13.1, 9.0, 67.9),
        ("2026-07", 58.6, 17.2, 10.6, 86.4),
    ], first=True)


def _build_api_calls(wb) -> None:
    _xlsx_sheet(wb, "调用量", [
        ("月份", "调用次数（万次）", "峰值QPS", "错误率"),
        ("2026-04", 36800, 11400, "0.42%"),
        ("2026-05", 39400, 12100, "0.38%"),
        ("2026-06", 42100, 12800, "0.31%"),
    ], first=True)


def _build_courses(wb) -> None:
    _xlsx_sheet(wb, "课程目录", [
        ("课程编码", "课程名称", "类别", "学时", "是否必修"),
        ("TR-001", "信息安全通识", "合规", 2, "是"),
        ("TR-002", "数据分类分级实务", "合规", 2, "是"),
        ("TR-003", "反舞弊与廉洁从业", "合规", 1, "是"),
        ("TR-004", "急救与消防演练", "安全", 1, "是"),
        ("TR-011", "项目管理基础", "通用", 4, "否"),
        ("TR-012", "客户沟通技巧", "通用", 3, "否"),
        ("TR-021", "PyTorch 实战", "技术", 6, "否"),
        ("TR-022", "数据建模进阶", "技术", 5, "否"),
        ("STAT-000", "课程总计 46 门（必修 8 门）", "-", "-", "-"),
        ("STAT-001", "年度学时要求 40 学时", "-", "-", "-"),
    ], first=True)


def _build_candidates(wb) -> None:
    _xlsx_sheet(wb, "候选人漏斗", [
        ("岗位序列", "在流程人数", "已发 Offer", "到面率"),
        ("研发", 28, 8, "68%"),
        ("销售", 19, 6, "74%"),
        ("交付", 12, 2, "70%"),
        ("职能", 4, 1, "75%"),
        ("合计", 63, 17, "71%"),
    ], first=True)


XLSX_BUILDERS: dict[str, object] = {
    "table_hrcost_2026q2": _build_hrcost,
    "table_pricing": _build_pricing,
    "table_servers": _build_servers,
    "xlsx_finance_budget_2026": _build_budget,
    "xlsx_hr_headcount_2026": _build_headcount,
    "xlsx_ops_capacity_plan": _build_capacity,
    "xlsx_product_metrics_2026": _build_product_metrics,
    "xlsx_legal_contract_register": _build_contract_register,
    "xlsx_sales_pipeline_2026h2": _build_pipeline,
    "table_cloud_cost_2026": _build_cloud_cost,
    "table_api_calls_2026": _build_api_calls,
    "table_training_courses": _build_courses,
    "table_candidate_pipeline": _build_candidates,
}


def gen_xlsx() -> None:
    import openpyxl

    n = 0
    for d in DOCS:
        if d["format"] != "xlsx":
            continue
        build = XLSX_BUILDERS.get(d["doc_id"])
        if build is None:
            raise KeyError(f"[MISSING_GENERATOR] {d['doc_id']} 缺 XLSX 构建器")
        p = FILES_DIR / d["file"]
        p.parent.mkdir(parents=True, exist_ok=True)
        wb = openpyxl.Workbook()
        build(wb)
        wb.save(str(p))
        n += 1
    print(f"    XLSX {n} 份（含多 Sheet）")


# CSV 定义注册表：doc_id → (表头, 数据行, 写盘编码)
CSV_DEFS: dict[str, tuple] = {
    # ---- 探路版 3 份（逐字保留，UTF-8 BOM）----
    "table_sales_2026": (
        ["月份", "区域", "销售额（万元）", "完成率"],
        [("2026-04", "华东", 103.2, "88.1%"), ("2026-04", "华南", 96.8, "91.4%"),
         ("2026-05", "华东", 141.2, "96.5%"), ("2026-05", "华南", 118.5, "93.2%"),
         ("2026-06", "华东", 168.4, "102.7%"), ("2026-06", "华南", 122.7, "95.9%")],
        "utf-8-sig"),
    "table_expense_h1_2026": (
        ["报销单号", "申请人", "部门", "金额（元）", "月份"],
        [("BX-2026-0132", "王倩", "市场部", 3860.00, "2026-03"),
         ("BX-2026-0207", "陈斌", "研发中心", 5210.50, "2026-04"),
         ("BX-2026-0356", "苏黎", "产品部", 2478.00, "2026-05"),
         ("BX-2026-0411", "李洪", "客服部", 1905.00, "2026-06")],
        "utf-8-sig"),
    "table_tickets_2026": (
        ["月份", "工单量", "解决率", "平均响应（秒）"],
        [("2026-01", 4210, "91.2%", 52), ("2026-02", 3864, "90.8%", 49),
         ("2026-03", 4502, "92.1%", 47), ("2026-04", 4688, "93.0%", 46),
         ("2026-05", 4933, "92.6%", 44), ("2026-06", 5120, "93.8%", 41)],
        "utf-8-sig"),

    # ---- 扩容新增：中文编码专项（GBK / GB18030 / UTF-8 无 BOM）----
    # 注：本表必须有数值列——否则所有单元格均为字符串，会被多级表头启发式
    #（前 3 行数值占比 < 20% 即判为表头）整段吞掉。
    "csv_gbk_supplier_contacts": (
        ["供应商名称", "等级", "联系人", "联系电话", "主营类目",
         "合作起始年份", "年度采购额（万元）"],
        [("恒信电子设备有限公司", "A", "张伟", "0591-8823-6611", "服务器整机", 2019, 860),
         ("南方精密制造股份", "A", "刘敏", "020-3877-2244", "机柜与结构件", 2020, 540),
         ("弘图软件服务", "A", "赵鹏", "010-6255-8890", "中间件与数据库", 2021, 730),
         ("长风网络科技", "A", "孙倩", "0571-8800-3312", "网络设备", 2018, 420),
         ("明源仓储服务", "B", "周涛", "0592-5567-1180", "仓储与配送", 2022, 260),
         ("中新耗材供应", "B", "吴静", "0755-8392-4471", "办公耗材", 2023, 95),
         ("汇总", "A 类 4 家 / 合计 6 家", "", "", "最早合作 2018 年", 2018, 2905)],
        "gbk"),
    "csv_gbk_warehouse_inventory": (
        ["SKU", "品名", "在库数量", "安全库存", "是否预警"],
        [("GD-2021-0133", "机柜 PDU 电源条", 84, 40, "否"),
         ("GD-2022-0457", "光模块 10G", 26, 30, "是"),
         ("GD-2023-0201", "理线架", 12, 25, "是"),
         ("GD-2023-0912", "服务器导轨", 63, 30, "否"),
         ("GD-2024-0079", "网线 Cat6 整箱", 41, 20, "否"),
         ("GD-2024-0318", "标签打印机色带", 18, 25, "是"),
         ("汇总", "预警 SKU 3 项 / 在库 SKU 6 类", "", "", "")],
        "gbk"),
    "csv_gbk_channel_sales_2025": (
        ["渠道类型", "销售额（万元）", "占比", "同比"],
        [("代理渠道", 3240, "48.6%", "+19.2%"),
         ("直销团队", 2180, "32.7%", "+14.6%"),
         ("线上自助", 830, "12.5%", "+38.4%"),
         ("生态合作", 416, "6.2%", "-4.1%")],
        "gb18030"),
    "csv_gbk_hr_training_records": (
        ["场次编号", "培训日期", "主题", "参训人数", "学时"],
        [("TR-2026H1-01", "2026-02-18", "信息安全通识", 46, 2),
         ("TR-2026H1-02", "2026-03-25", "数据分类分级实务", 38, 2),
         ("TR-2026H1-03", "2026-04-21", "项目管理基础", 52, 4),
         ("TR-2026H1-04", "2026-05-16", "反舞弊与廉洁从业", 61, 1),
         ("TR-2026H1-05", "2026-06-25", "数据安全合规宣贯", 45, 2),
         ("汇总", "2026H1 共 5 场", "累计参训 242 人次", "", "")],
        "gbk"),
    "csv_utf8_nobom_product_catalog": (
        ["产品编码", "产品名称", "版本", "年费（元/年）", "状态"],
        [("PD-001", "星辰盾", "v3.2", 12999, "在售"),
         ("PD-002", "星辰数据接入平台", "v1.4", 3999, "在售"),
         ("PD-003", "星辰工单助手", "v2.1", 999, "在售"),
         ("PD-004", "星辰语音转写", "v0.9", 0, "公测"),
         ("PD-005", "星辰报表中心", "v2.6", 2999, "在售"),
         ("PD-006", "星辰流程引擎", "v1.1", 1999, "在售"),
         ("汇总", "在售产品 9 个", "旗舰：星辰盾 v3.2", "", "")],
        "utf-8"),

    # ---- 扩容新增：常规 UTF-8 BOM 表 7 份 ----
    "table_headcount_monthly": (
        ["月份", "月初人数", "月末人数", "离职率"],
        [("2026-01", 478, 482, "1.1%"), ("2026-02", 482, 489, "0.9%"),
         ("2026-03", 489, 496, "1.3%"), ("2026-04", 496, 503, "1.0%"),
         ("2026-05", 503, 508, "0.8%"), ("2026-06", 508, 512, "1.2%")],
        "utf-8-sig"),
    "table_marketing_spend_2026": (
        ["月份", "渠道", "投放金额（万元）", "获客数", "单线索成本（元）"],
        [("2026-04", "搜索竞价", 218, 5180, 421),
         ("2026-04", "内容社区", 96, 3120, 308),
         ("2026-05", "搜索竞价", 232, 5620, 413),
         ("2026-05", "内容社区", 102, 3340, 305),
         ("2026-06", "搜索竞价", 286, 7410, 386),
         ("2026-06", "内容社区", 118, 3960, 298)],
        "utf-8-sig"),
    "table_invoice_records_2026": (
        ["月份", "开票金额（万元）", "作废张数", "红冲张数"],
        [("2026-01", 2860, 2, 1), ("2026-02", 2410, 3, 0),
         ("2026-03", 3380, 1, 2), ("2026-04", 3120, 2, 1),
         ("2026-05", 3610, 4, 1), ("2026-06", 3820, 2, 0),
         ("合计", 19200, 14, 5)],
        "utf-8-sig"),
    "table_customer_satisfaction_2026": (
        ["月份", "NPS", "满意率", "投诉数"],
        [("2026-01", 39, "88.6%", 42), ("2026-02", 41, "89.2%", 37),
         ("2026-03", 42, "89.8%", 35), ("2026-04", 44, "90.5%", 29),
         ("2026-05", 45, "91.0%", 24), ("2026-06", 46, "91.3%", 21)],
        "utf-8-sig"),
    "table_purchase_orders_2026": (
        ["月份", "订单数", "采购金额（万元）", "平均交付天数"],
        [("2026-01", 32, 412, 26), ("2026-02", 28, 366, 24),
         ("2026-03", 41, 528, 28), ("2026-04", 38, 502, 25),
         ("2026-05", 47, 648, 23), ("2026-06", 50, 724, 22),
         ("合计", 236, 3180, 25)],
        "utf-8-sig"),
    "table_interview_records": (
        ["面试日期", "岗位", "面试官", "评分", "结论"],
        [("2026-04-08", "后端工程师", "陈斌", 4.2, "通过"),
         ("2026-04-22", "后端工程师", "王倩", 3.6, "淘汰"),
         ("2026-05-13", "算法工程师", "陈斌", 4.6, "通过"),
         ("2026-05-27", "前端工程师", "苏黎", 4.1, "通过"),
         ("2026-06-10", "运维工程师", "王倩", 3.9, "待定"),
         ("2026-06-24", "算法工程师", "陈斌", 4.4, "通过")],
        "utf-8-sig"),
    # 同 csv_gbk_supplier_contacts：需数值列避免被判为多级表头
    "legal_trademark_list": (
        ["类别", "注册号 / 备案号", "名称", "有效期至", "核准年份", "剩余年限"],
        [("商标", "第 3812xxxx 号", "星辰盾", "2031-04-20", 2021, 5),
         ("商标", "第 4025xxxx 号", "星辰数据接入", "2032-08-13", 2022, 6),
         ("商标", "第 4480xxxx 号", "星辰工单", "2033-02-27", 2023, 7),
         ("域名", "闽 ICP 备 2021xxxx 号", "xingchenyun.com", "2027-05-11", 2022, 1),
         ("域名", "闽 ICP 备 2021xxxx 号", "xingchen-cloud.cn", "2027-05-11", 2022, 1),
         ("汇总", "注册商标 23 件 · 域名 17 个", "", "", 2021, 20)],
        "utf-8-sig"),
}


def gen_csv() -> None:
    n, enc_stat = 0, {}
    for d in DOCS:
        if d["format"] != "csv":
            continue
        spec = CSV_DEFS.get(d["doc_id"])
        if spec is None:
            raise KeyError(f"[MISSING_GENERATOR] {d['doc_id']} 缺 CSV 定义")
        header, rows, encoding = spec
        write_csv(d["file"], header, rows, encoding)
        enc_stat[encoding] = enc_stat.get(encoding, 0) + 1
        n += 1
    print(f"    CSV {n} 份（编码分布 {enc_stat}）")


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

    # ==================== 扩容新增 73 份（27 → 100）====================
    # ---- Legal（含 DOCX / PDF / CSV）----
    "legal_nnn_agreement": (["协议编号XC-NDA-2026-007", "保密期限5年",
                             "违约金50万元", "生效日期2026-02-09"],
                            ["协议编号：XC-NDA-2026-007", "5 年"]),
    "legal_ip_assignment": (["转让总价80万元", "3项发明专利2项软件著作权",
                             "权属变更完成2025-11-20"],
                            ["80 万元", "2025-11-20"]),
    "legal_purchase_contract_zhongke": (["合同编号XC-CG-2026-014", "GPU服务器12台",
                                         "总价468万元", "交货期45天", "质保3年",
                                         "逾期违约金每日0.5%上限5%"],
                                        ["12 台", "468 万元", "45 天"]),
    "legal_settlement_memo": (["编号XC-HJ-2026-003", "一次性支付35万元",
                               "签署日期2026-07-18"],
                              ["35 万元", "2026-07-18"]),
    "legal_labor_contract_template": (["首次合同期限3年含试用期6个月",
                                       "竞业限制不超过2年",
                                       "竞业补偿按离职前十二个月平均工资30%",
                                       "工资支付日次月15日"],
                                      ["3 年", "6 个月", "30%"]),
    "legal_dpa_processing": (["协议编号XC-DPA-2026-002", "子处理者提前15个工作日告知",
                              "服务终止后90天内删除数据", "年度审计每年1次"],
                             ["XC-DPA-2026-002", "15 个工作日", "90 天"]),
    "legal_case_litigation_2026": (["在办案件3起", "最高标的额1,180万元",
                                    "蓝鲸数据案开庭2026-04-22"],
                                   ["3 起", "1,180 万元"]),
    "legal_software_license": (["授权500个命名用户席位", "许可期2026-01-01至2028-12-31",
                                "年许可费96万元"],
                               ["500 个命名用户席位", "96 万元"]),
    "layout_tender_evaluation": (["评标委员会5名成员外部专家不少于2名",
                                  "技术方案45%商务报价35%履约能力20%",
                                  "有效投标人不足3家流标"],
                                 ["45%", "35%", "20%"]),
    "legal_compliance_review_2026": (["高风险3项中风险7项低风险15项",
                                      "高风险整改截止2026-11-30", "出具日期2026-09-01"],
                                     ["2026-11-30", "表 3-1"]),
    "legal_data_export_assessment": (["接收方为新加坡子公司", "出境规模12.4万条/年",
                                      "评估结论可通过有效期2年",
                                      "标准合同备案日期2026-05-26"],
                                     ["12.4 万条", "新加坡子公司", "2 年"]),
    "legal_trademark_list": (["注册商标23件", "域名17个", "星辰盾商标有效期至2031-04-20"],
                             ["23 件", "17 个"]),
    "contract_annual_maintenance": (["合同编号XC-HT-2026-027", "服务方维智科技",
                                     "服务期2026-01-01至2026-12-31",
                                     "年度服务费128万元按季支付",
                                     "P1级15分钟内响应2小时内恢复"],
                                    ["XC-HT-2026-027", "128 万元"]),

    # ---- Policy（含 DOCX / PDF / TXT）----
    "policy_export_control": (["受控物项清单每季度更新", "筛查记录保存10年",
                               "违规直接责任人当年度绩效一票否决"],
                              ["每季度", "10 年"]),
    "policy_recruitment": (["编制HC由CEO终审", "内推奖励8,000元/人",
                            "核心岗位背调覆盖率100%"],
                           ["CEO 终审", "8,000 元"]),
    "policy_performance_appraisal": (["半年度考核1月与7月", "A档不超过20%", "D档不低于5%",
                                      "连续两次D进入PIP改进期3个月"],
                                     ["20%", "5%", "3 个月"]),
    "policy_software_asset": (["生产环境软件正版化率目标100%", "每年12月全公司盘点"],
                              ["100%", "每年 12 月"]),
    "policy_expense_v1": (["报销时限60日内", "两级审批：部门负责人→财务部",
                           "每月15日与月末两次集中支付"],
                          ["60 日"]),
    "policy_expense_v2": (["报销时限30日内", "单笔超5万元加签财务总监",
                           "电子发票须系统查重"],
                          ["30 日", "5 万元"]),
    "policy_expense_v3": (["报销时限20个工作日内", "单笔达5,000元须附合同或验收说明",
                           "两级审批超5万元加签财务总监"],
                          ["20 个工作日", "5,000 元"]),
    "policy_overtime_rnd": (["每月加班不超过36小时", "工作日加班1.5倍",
                             "休息日不能补休的2倍"],
                            ["36 小时", "1.5 倍"]),
    "policy_overtime_cs": (["28天为一周期轮班制", "每月加班不超过24小时",
                            "法定节假日3倍", "夜班津贴60元/班次"],
                           ["24 小时", "3 倍", "60 元"]),
    "policy_infosec_v2": (["密级仍为public/internal/confidential三级",
                           "新增远程终端管控与全盘加密", "第三方人员受限账号录屏留痕",
                           "禁止将confidential级数据输入外部大模型",
                           "移动存储介质生产区禁用"],
                          ["2026-07-01", "三级"]),
    "policy_remote_work": (["每人每月远程办公不超过6天", "提前1个工作日OA审批",
                            "核心在线时段10:00至16:00"],
                           ["6 天", "10:00 至 16:00"]),
    "policy_data_classification": (["五级分类：客户/交易/产品/员工/运营数据",
                                    "四级分级：公开/内部/敏感/核心",
                                    "核心数据AES-256加密存储"],
                                   ["四级", "AES-256"]),

    # ---- SOP（含 DOCX / PDF / TXT）----
    "sop_refund_processing": (["审核3个工作日内完成", "超5万元报财务总监审批",
                               "到账周期7至15个工作日"],
                              ["3 个工作日", "5 万元"]),
    "sop_vendor_onboarding": (["准入须提交5项材料", "履约保证金10万元",
                               "15个工作日内完成审核"],
                              ["5 项材料", "10 万元"]),
    "sop_incident_response_v1": (["P1须4小时内恢复服务", "P2 12小时", "P3 3个工作日",
                                  "邮件+短信双通道30分钟内首次通报"],
                                 ["4 小时", "XC-SOP-IT-2024-006"]),
    "sop_incident_response_v2": (["P1须2小时内恢复服务", "P2 8小时", "P3 2个工作日",
                                  "三种通道通报首次不超过15分钟"],
                                 ["2 小时", "15 分钟"]),
    "sop_incident_response_v3": (["P1须90分钟内恢复服务", "首次通报不超过15分钟",
                                  "主值10分钟未响应升级替补", "替补须15分钟内接手"],
                                 ["90 分钟", "10 分钟", "XC-SOP-IT-2026-003"]),
    "sop_release_deploy": (["发布窗口周二周四20:00至24:00", "灰度5%→20%→100%",
                            "每阶段观察不少于30分钟", "回滚须15分钟内执行完毕"],
                           ["5% → 20% → 100%", "30 分钟"]),
    "sop_backup_recovery_drill": (["RTO 4小时RPO 15分钟", "每半年演练一次",
                                   "最近一次2026-05-20耗时2小时48分"],
                                  ["RTO", "2026-05-20"]),
    "sop_interview_hiring": (["常规岗位3轮面试", "面评24小时内提交",
                              "Offer有效期7天", "总监级另加一轮高管面"],
                             ["3 轮", "24 小时"]),
    "sop_data_destruction": (["纸质物理粉碎电子介质逻辑覆写3次后消磁",
                              "部门负责人+数据安全官双签", "销毁记录保存5年"],
                             ["5 年", "双签"]),
    "sop_access_review": (["每季度复核一次覆盖全部特权账号",
                           "临时账号操作后24小时内回收", "复核记录保存2年"],
                          ["2 年", "24 小时"]),
    "sop_change_management": (["标准/常规/紧急三级变更",
                               "紧急变更处置后24小时内补齐审批",
                               "月第二个周三14:00评审会", "P1P2异常15分钟内回滚"],
                              ["24 小时", "15 分钟"]),

    # ---- Report / Manual / Spec / FAQ ----
    "report_incident_2026q2": (["二季度安全事件14起其中P1 1起",
                                "网关鉴权绕行影响账户1,243个", "平均检测时长23分钟"],
                               ["14 起", "1,243 个", "23 分钟"]),
    "report_product_annual_2025": (["全年发布版本27个大版本4个", "年末付费客户1,842家",
                                    "NPS 46", "P1级缺陷9个", "数据接入平台1.0于2025-09-12发布"],
                                   ["1,842", "46", "2025-09-12"]),
    "report_board_resolution_2026": (["2026年度资本性支出预算3,200万元",
                                      "新加坡子公司首期注册资本200万新元",
                                      "授权有效期12个月"],
                                     ["3,200 万元", "200 万新元"]),
    "report_performance_test_2026": (["目标TPS 3,000实测峰值3,860超出28.7%",
                                      "下单接口P99 218毫秒", "建议限流阈值上调至4,200 TPS"],
                                     ["3,860", "218 毫秒"]),
    "layout_report_fin_annual_2025": (["2025年营业收入4.12亿元", "净利润6,840万元",
                                       "毛利率58.4%", "研发投入1.02亿元占24.8%",
                                       "期末总资产6.42亿元资产负债率38.7%"],
                                      ["4.12 亿元", "24.8%", "表 3-1"]),
    "layout_manual_employee_handbook": (["2026版v6.2施行日期2026-01-01",
                                         "通讯补贴每月150元", "年度体检9至10月",
                                         "每年不少于40学时", "正式员工离职提前30日"],
                                       ["150 元", "40 学时", "9 月至 10 月"]),
    "layout_spec_api_gateway": (["网关版本v3.2", "单请求体上限2MB", "默认超时3秒",
                                 "限流租户+接口500 QPS", "JWT令牌2小时刷新令牌7天"],
                                ["2MB", "3 秒", "表 2-1"]),
    "manual_ops_runbook": (["版本v2026.08", "变更窗口周二周四20:00至24:00",
                            "一级告警5分钟内通知值班经理", "核心业务库备份保留30天",
                            "跨季度首周备份额外保留1年"],
                           ["20:00 至 24:00", "5 分钟", "30 天"]),
    "spec_data_warehouse": (["规范版本v3.1生效2026-03-01", "ODS/DWD/DWS/ADS四层禁止跨层直连",
                             "基线任务每日06:30前完成", "失败重试最多2次间隔5分钟",
                             "波动率超过30%阻断下游"],
                            ["ODS / DWD / DWS / ADS", "06:30", "30%"]),
    "faq_finance_reimbursement": (["抬头或税号错误发票不予受理",
                                   "单笔1万元以上须附合同或采购审批说明",
                                   "同一单可重提3次"],
                                  ["1 万元", "3 次"]),

    # ---- Notes / FAQ / Reference（TXT）----
    "notes_weekly_ops_2026": (["本周处理告警37条二级告警4条",
                               "停机维护窗口2026-09-12 22:00至23:30",
                               "遗留问题3项责任人王倩闭环2026-09-19前"],
                              ["2026-09-12", "22:00 至 23:30"]),
    "notes_strategy_retreat_2026": (["会议时间2026-08-14至08-15",
                                     "地点福州·闽江畔会议中心",
                                     "AI算力预算预留1,500万元",
                                     "战略解码会2026-09-25"],
                                    ["1,500 万元", "2026-09-25"]),
    "faq_it_helpdesk": (["IT热线0591-8888-6600", "热线15分钟内响应",
                         "VPN有效期180天", "邮箱默认50GB"],
                        ["0591-8888-6600", "180 天"]),
    "dict_order_domain": (["o_order_no格式OR+14位时间戳+4位流水号",
                           "o_status五种枚举值", "o_amount单位为分"],
                          ["OR + 14 位时间戳"]),

    # ---- 表格：XLSX（多 Sheet / 单 Sheet）----
    "xlsx_finance_budget_2026": (["2026营业收入预算52,000万元", "净利润预算8,100万元",
                                  "人力成本预算12,400万元", "4个工作表"],
                                 ["52,000 万元", "8,100 万元", "收入预算"]),
    "xlsx_hr_headcount_2026": (["合计编制560在职512缺口48",
                                "研发中心编制232在职214", "3个工作表"],
                               ["560", "512", "招聘进度"]),
    "xlsx_ops_capacity_plan": (["A1机房46节点冗余28%", "B2机房38节点冗余21%",
                                "CPU平均63%峰值78%阈值≤70%", "扩容合计24节点1,230万元"],
                               ["46", "28%", "扩容计划"]),
    "xlsx_product_metrics_2026": (["2026-06 MAU 124,000 DAU 24,600付费租户3,120",
                                   "2026-06月留存68.2%", "企业版ARPU 8,600元/月"],
                                  ["124,000", "24,600", "收入贡献"]),
    "xlsx_legal_contract_register": (["在册合同187份高风险3份",
                                      "中科智联合同468万元",
                                      "待履约交付节点14个"],
                                     ["187 份", "468", "风险清单"]),
    "xlsx_sales_pipeline_2026h2": (["合计商机金额8,600万元加权3,240万元",
                                    "华东3,800万元预计赢率62%", "即将签约商机6个500万元"],
                                   ["8,600", "3,240", "漏斗阶段"]),
    "table_cloud_cost_2026": (["2026-07云账单合计86.4万元", "2026-05合计62.2万元"],
                              ["86.4", "2026-07"]),
    "table_api_calls_2026": (["2026-06调用42,100万次峰值QPS 12,800",
                              "2026-06错误率0.31%"],
                             ["42,100", "12,800"]),
    "table_training_courses": (["课程总计46门必修8门", "年度学时要求40学时",
                                "合规类课程3门"],
                               ["46 门", "40 学时"]),
    "table_candidate_pipeline": (["在流程合计63人已发Offer 17份", "整体到面率71%",
                                  "研发序列在流程28人"],
                                 ["63", "17", "71%"]),

    # ---- 表格：CSV（GBK / GB18030 / UTF-8 无 BOM / UTF-8-sig）----
    "csv_gbk_supplier_contacts": (["共6家供应商其中A类4家", "GBK编码"],
                                  ["A 类 4 家", "合计 6 家"]),
    "csv_gbk_warehouse_inventory": (["在库SKU 6类预警SKU 3项", "光模块10G低于安全库存",
                                     "GBK编码"],
                                    ["预警 SKU 3 项"]),
    "csv_gbk_channel_sales_2025": (["代理渠道3,240万元占48.6%", "线上自助同比+38.4%",
                                    "GB18030编码"],
                                   ["3,240", "48.6%"]),
    "csv_gbk_hr_training_records": (["2026H1共5场累计参训242人次", "GBK编码"],
                                    ["5 场", "242 人次"]),
    "csv_utf8_nobom_product_catalog": (["在售产品9个旗舰星辰盾v3.2", "UTF-8无BOM编码"],
                                       ["9 个", "星辰盾"]),
    "table_headcount_monthly": (["2026-06月末512人", "月度离职率区间0.8%~1.3%"],
                                ["512", "2026-06"]),
    "table_marketing_spend_2026": (["2026-06搜索竞价286万元单线索成本386元",
                                    "内容社区单位获客成本更低"],
                                   ["286", "386"]),
    "table_invoice_records_2026": (["上半年开票合计19,200万元", "作废14张红冲5张"],
                                   ["19,200", "14"]),
    "table_customer_satisfaction_2026": (["2026-06 NPS 46满意率91.3%", "投诉数降至21件"],
                                         ["46", "91.3%"]),
    "table_purchase_orders_2026": (["合计236单3,180万元", "平均交付25天",
                                    "交付天数逐月下降"],
                                   ["236", "3,180"]),
    "table_interview_records": (["共6场面试通过3人", "最高分4.6分为算法工程师"],
                                ["4.6", "通过"]),

    # ---- 扫描件新增 2 份 ----
    "scan_expense_claim_form": (["报销单号BX-2026-0518申请人苏黎", "报销金额3,480.00元",
                                 "审批完成日期2026-08-09"],
                                ["BX-2026-0518", "2026-08-09"]),
    "scan_training_signin": (["培训日期2026-06-25主题数据安全合规宣贯",
                              "应到48人实到45人请假3人", "讲师李洪"],
                             ["2026-06-25", "48 人", "45 人"]),
}


def build_manifest() -> dict:
    for d in DOCS:
        # CSV 编码以 CSV_DEFS 为唯一数据源回写到清单元数据（避免两处各写一遍）
        if d["format"] == "csv":
            spec = CSV_DEFS.get(d["doc_id"])
            d["encoding"] = spec[2] if spec else "utf-8-sig"
        if d["doc_id"] not in MANIFEST_ANNOTATIONS:
            raise KeyError(f"[MISSING_ANNOTATION] {d['doc_id']} 缺 MANIFEST_ANNOTATIONS 标注")
        kf, anchors = MANIFEST_ANNOTATIONS[d["doc_id"]]
        d["key_facts"] = kf
        d["section_anchors"] = anchors
    scanned = sorted(d["doc_id"] for d in DOCS if d["is_scanned"])
    complex_layout = sorted(d["doc_id"] for d in DOCS if d.get("layout") == "complex")
    multi_page = sorted(d["doc_id"] for d in DOCS if d.get("layout") == "multi_page")
    gbk_csv = sorted(d["doc_id"] for d in DOCS
                     if d["format"] == "csv" and str(d.get("encoding", "")).startswith("gb"))
    return {
        "version": "0.2.0-full",
        "name": "rag_100_docs",
        "kb_id": KB_ID,
        "generated_at": "2026-09-17",
        "note": ("R3 全量版语料清单（100 份，由 27 份探路版扩容而来）。MD/TXT 的关键事实"
                 "在生成期固化在源内容中；is_scanned=true 的 PDF 为文字渲染成图像封装"
                 "（无真实 OCR，在线 OCR 回填结果应与 key_facts 一致）。"
                 "layout=complex 为复杂版面 PDF（双栏 + 表格 + 页眉页脚页码），"
                 "layout=multi_page 为多页长文；CSV 含 GBK/GB18030/UTF-8 无 BOM/"
                 "UTF-8-sig 四种编码。expected_chunk_ids 需索引后回填。"),
        "documents": DOCS,
        "groups": {
            # ---- 探路版三组（保留）----
            "similar_pair": SIMILAR_PAIRS["pair_attendance"],
            "version_chain": VERSION_CHAINS["chain_travel"],
            "scanned": scanned,
            # ---- 扩容：更多相似对 / 版本链 / 跨文档关联组 ----
            "similar_pair_overtime": SIMILAR_PAIRS["pair_overtime"],
            "version_chain_incident_sop": VERSION_CHAINS["chain_incident_sop"],
            "version_chain_expense": VERSION_CHAINS["chain_expense"],
            "version_chain_infosec": VERSION_CHAINS["chain_infosec"],
            "cross_doc": {k: v for k, v in CROSS_DOC_GROUPS.items()},
            # ---- 形态专项（§4 覆盖清单）----
            "complex_layout_pdf": complex_layout,
            "multi_page_long_doc": multi_page,
            "gbk_encoded_csv": gbk_csv,
        },
        "format_counts": {},
    }


# ---------------------------------------------------------------- 校验

QUERY_TYPES = {"faq", "exact_id", "multi_condition", "table_value",
               "cross_doc", "low_confidence", "no_evidence"}
REQUIRED_FIELDS = ["doc_id", "expected_doc_ids", "expected_chunk_ids", "key_facts",
                   "query_type", "should_refuse", "version_requirement", "permission_scope"]


EXPECTED_TOTAL = 100          # 任务目标：27 → 100 份
PERMISSION_LEVELS = {"general", "hr_confidential", "finance_restricted",
                     "legal_confidential", "it_admin"}


def validate(dataset: dict, manifest: dict) -> list[str]:
    errs: list[str] = []
    doc_ids = {d["doc_id"] for d in manifest["documents"]}

    # ---- 一、总量与格式覆盖 ----
    if len(manifest["documents"]) != EXPECTED_TOTAL:
        errs.append(f"文档总数应为 {EXPECTED_TOTAL}，实际 {len(manifest['documents'])}")
    if len(doc_ids) != len(manifest["documents"]):
        errs.append("存在重复 doc_id")

    fc: dict[str, int] = {}
    for d in manifest["documents"]:
        fc[d["format"]] = fc.get(d["format"], 0) + 1
    manifest["format_counts"] = fc
    for fmt in ("pdf", "docx", "md", "txt", "xlsx", "csv"):
        if fc.get(fmt, 0) == 0:
            errs.append(f"缺少格式: {fmt}")

    # ---- 二、文件落盘与清单双向匹配 ----
    listed = {d["file"] for d in manifest["documents"]}
    on_disk = {str(p.relative_to(FILES_DIR)).replace("\\", "/")
               for p in FILES_DIR.rglob("*") if p.is_file()}
    missing = sorted(listed - on_disk)
    orphan = sorted(on_disk - listed)
    if missing:
        errs.append(f"清单有、磁盘无的文件 {len(missing)} 个: {missing[:8]}"
                    f"{' ...' if len(missing) > 8 else ''}")
    if orphan:
        errs.append(f"磁盘有、清单无的孤儿文件 {len(orphan)} 个: {orphan[:8]}"
                    f"{' ...' if len(orphan) > 8 else ''}")

    # ---- 三、每份文档的必备元数据 ----
    for d in manifest["documents"]:
        if not d.get("key_facts"):
            errs.append(f"{d['doc_id']}: key_facts 为空")
        if d["permission_scope"] not in PERMISSION_LEVELS:
            errs.append(f"{d['doc_id']}: 未知权限标记 {d['permission_scope']}")
        if d["format"] == "csv" and not d.get("encoding"):
            errs.append(f"{d['doc_id']}: CSV 缺 encoding 标注")

    # ---- 四、版本链 / 相似对 / 跨文档组：引用不得悬空，链长度须 ≥2 ----
    g = manifest["groups"]
    group_items: list[tuple[str, list[str]]] = [
        ("similar_pair", g["similar_pair"]),
        ("version_chain", g["version_chain"]),
        ("scanned", g["scanned"]),
        ("similar_pair_overtime", g["similar_pair_overtime"]),
        ("version_chain_incident_sop", g["version_chain_incident_sop"]),
        ("version_chain_expense", g["version_chain_expense"]),
        ("version_chain_infosec", g["version_chain_infosec"]),
        ("complex_layout_pdf", g["complex_layout_pdf"]),
        ("multi_page_long_doc", g["multi_page_long_doc"]),
        ("gbk_encoded_csv", g["gbk_encoded_csv"]),
    ]
    for name, items in group_items:
        for doc_id in items:
            if doc_id not in doc_ids:
                errs.append(f"group[{name}] 引用不存在的 doc_id: {doc_id}")
    for name in ("similar_pair", "similar_pair_overtime"):
        if len(g[name]) != 2:
            errs.append(f"相似文档组 {name} 必须恰好 2 份，实际 {len(g[name])}")
    for name in ("version_chain", "version_chain_incident_sop",
                 "version_chain_expense", "version_chain_infosec"):
        if len(g[name]) < 2:
            errs.append(f"版本链 {name} 至少 2 份，实际 {len(g[name])}")
    if len(g["scanned"]) < 2:
        errs.append("扫描件至少 2 份")
    if len(g["complex_layout_pdf"]) == 0:
        errs.append("缺少复杂版面 PDF")
    if len(g["multi_page_long_doc"]) == 0:
        errs.append("缺少多页长文")
    if len(g["gbk_encoded_csv"]) == 0:
        errs.append("缺少 GBK/GB18030 编码 CSV")

    # 版本链元数据自洽：supersedes / superseded_by 互指且不悬空
    for doc_id, items in VERSION_CHAINS.items():
        for i, cur in enumerate(items):
            v = next((d["version"] for d in manifest["documents"]
                      if d["doc_id"] == cur and d.get("version")), None)
            if v is None:
                errs.append(f"版本链 {doc_id} 中 {cur} 缺 version 元数据")
                continue
            prev_expected = items[i - 1] if i > 0 else None
            next_expected = items[i + 1] if i + 1 < len(items) else None
            if v.get("supersedes") != prev_expected:
                errs.append(f"{cur}: version.supersedes 应为 {prev_expected}，"
                            f"实际 {v.get('supersedes')}")
            if v.get("superseded_by") != next_expected:
                errs.append(f"{cur}: version.superseded_by 应为 {next_expected}，"
                            f"实际 {v.get('superseded_by')}")

    # 跨文档组：组内至少 2 份且全部存在
    for name, items in CROSS_DOC_GROUPS.items():
        if len(items) < 2:
            errs.append(f"跨文档组 {name} 至少 2 份")
        for doc_id in items:
            if doc_id not in doc_ids:
                errs.append(f"跨文档组 {name} 引用不存在的 doc_id: {doc_id}")

    # ---- 五、表格类文件必须能被现有管线读通（CSV 全量 + XLSX 抽检）----
    # 5.1 GBK/GB18030 CSV：必须能用声明的编码读出来
    for doc_id in g["gbk_encoded_csv"]:
        rel = next(d["file"] for d in manifest["documents"] if d["doc_id"] == doc_id)
        enc = next(d["encoding"] for d in manifest["documents"] if d["doc_id"] == doc_id)
        p = FILES_DIR / rel
        if not p.exists():
            continue
        try:
            with open(p, "r", encoding=enc, newline="") as fh:
                rows = list(csv.reader(fh))
            if len(rows) < 2 or not rows[0]:
                errs.append(f"{doc_id}: 按 {enc} 读取后为空表或只有表头")
        except UnicodeDecodeError as e:
            errs.append(f"{doc_id}: 按 {enc} 读取失败（非该编码）: {e}")

    # 5.2 全部 CSV：每一行必须与表头等宽（CsvParser 用 pandas，列数不符会整表读失败）
    for d in manifest["documents"]:
        if d["format"] != "csv":
            continue
        p = FILES_DIR / d["file"]
        if not p.exists():
            continue
        with open(p, "r", encoding=d["encoding"], newline="") as fh:
            rows = [r for r in csv.reader(fh) if r]
        if not rows:
            errs.append(f"{d['doc_id']}: CSV 为空")
            continue
        width = len(rows[0])
        bad = [(i + 1, len(r)) for i, r in enumerate(rows) if len(r) != width]
        if bad:
            errs.append(f"{d['doc_id']}: CSV 列数不一致（表头 {width} 列，"
                        f"异常行 [行号:列数] {bad[:5]}）")

    # 5.3 XLSX：必须能被 ExcelParser 打开且 sheet 数与声明一致
    for d in manifest["documents"]:
        if d["format"] != "xlsx":
            continue
        p = FILES_DIR / d["file"]
        if not p.exists():
            continue
        try:
            import openpyxl
            wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
            n_sheet = len(wb.sheetnames)
            wb.close()
        except Exception as e:
            errs.append(f"{d['doc_id']}: XLSX 打开失败 {type(e).__name__}: {e}")
            continue
        declared = d.get("sheets")
        if declared is not None and declared != n_sheet:
            errs.append(f"{d['doc_id']}: 声明 {declared} 个 sheet，实际 {n_sheet} 个")
        if n_sheet == 0:
            errs.append(f"{d['doc_id']}: XLSX 无工作表")

    # ---- 六、用例校验 ----
    covered: set[str] = set()
    seen_ids: set[str] = set()
    for c in dataset["test_cases"]:
        cid, ann = c["id"], c.get("annotation", {})
        if cid in seen_ids:
            errs.append(f"用例 id 重复: {cid}")
        seen_ids.add(cid)
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
        if not ann["should_refuse"] and not ann["key_facts"]:
            errs.append(f"{cid}: 非拒答用例必须给出 key_facts")
        # 权限自洽（双向）：
        #   · 非拒答用例 → 请求者必须持有目标文档所需权限，否则必然被门禁挡掉；
        #   · permission 拒答用例 → 请求者必须「不」持有该权限，否则不会拒答。
        doc_perm = {d["doc_id"]: d["permission_scope"] for d in manifest["documents"]}
        asked = set(ann["permission_scope"])
        is_perm_refusal = ann["refusal_reason"] == "permission"
        for r in refs:
            need = doc_perm.get(r)
            if not need or need == "general":
                continue
            covers = need in asked
            if is_perm_refusal and covers:
                errs.append(f"{cid}: 声明了 {need} 权限却标为权限拒答，语义矛盾（{r}）")
            if not is_perm_refusal and not covers:
                errs.append(f"{cid}: 目标文档 {r} 需 {need} 权限，"
                            f"但用例 permission_scope 未声明，会被权限门禁挡掉")
        covered |= refs

    uncovered = doc_ids - covered
    if uncovered:
        errs.append(f"未被任何用例覆盖的文档: {sorted(uncovered)}")
    return errs


# ---------------------------------------------------------------- main

def apply_backfill(cases: list[dict]) -> int:
    """把已索引回填过的 expected_chunk_ids / anchors 写回 annotation。

    重生成不得把已回填成果清零：只有 BACKFILLED_CHUNK_IDS 里出现过的用例会被覆盖，
    其余用例保持 null，等下一轮 ingest 后再回填。
    """
    n = 0
    for c in cases:
        spec = BACKFILLED_CHUNK_IDS.get(c["id"])
        if spec is None:
            continue
        chunk_ids, anchors = spec
        c["annotation"]["expected_chunk_ids"] = list(chunk_ids)
        c["annotation"]["expected_chunk_anchors"] = list(anchors)
        n += 1
    return n


def main() -> int:
    print("== R3 全量版生成器（27 → 100 份）==")
    gen_md_txt();     print("[1/7] MD/TXT 完成")
    gen_docx();       print("[2/7] DOCX 完成")
    gen_pdf_text();   print("[3/7] 单栏文本 PDF 完成")
    gen_pdf_layout(); print("[4/7] 复杂版面 PDF（双栏/表格/页眉页脚）完成")
    gen_pdf_scan();   print("[5/7] 扫描件 PDF（文字渲染成图）完成")
    gen_xlsx();       print("[6/7] XLSX（含多 Sheet）完成")
    gen_csv();        print("[7/7] CSV（GBK/GB18030/UTF-8 无 BOM/UTF-8-sig）完成")

    # 注意：manifest 必须等 validate() 之后再落盘 —— validate 会回填 format_counts，
    # 且这样能保证校验不通过时不产生半成品文件（与 dataset 写盘的节奏一致）。
    manifest = build_manifest()

    dataset = {
        "version": "0.2.0-full",
        "name": "rag_100_docs",
        "kb_id": KB_ID,
        "_comment": (
            "R3 全量版测试集（任务书 §4，覆盖 100 份异构语料）。8 个标注字段位于每条"
            "用例的 annotation 内：doc_id / expected_doc_ids / expected_chunk_ids / "
            "key_facts(关键事实) / query_type(查询类型, 7 类) / should_refuse(是否拒答, "
            "辅以 refusal_reason) / version_requirement(版本要求) / permission_scope"
            "(权限范围)。expected 由 annotation 派生，供 harness 执行。"
            "expected_chunk_ids 在索引前不可知：已回填的用例由 BACKFILLED_CHUNK_IDS 保住，"
            "其余为 null，待 ingest 后回填。无答案用例 key_facts 为空且应拒答；"
            "权限用例的 should_refuse 以「general 权限视角」标注。"),
        "fixture_dir": "../fixtures/rag_100_docs/files",
        "documents_count": len(manifest["documents"]),
        "test_cases": [{**c, "expected": derive_expected(c)} for c in CASES],
    }
    n_backfilled = apply_backfill(dataset["test_cases"])
    # 回填发生在 annotation 上，expected 需重算一次以保持双 schema 一致
    for c in dataset["test_cases"]:
        c["expected"] = derive_expected(c)
    print(f"已回填并保住的 expected_chunk_ids：{n_backfilled} 条用例")

    errs = validate(dataset, manifest)
    if errs:
        print(f"校验失败（{len(errs)} 项）：")
        for e in errs:
            print("  -", e)
        return 1
    DATASET_PATH.write_text(
        json.dumps(dataset, ensure_ascii=False, indent=2), encoding="utf-8")
    (HERE / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    g = manifest["groups"]
    print(f"manifest.json 写入完成（{len(manifest['documents'])} 份文档，"
          f"复杂版面 {len(g['complex_layout_pdf'])} 份，多页长文 {len(g['multi_page_long_doc'])} 份，"
          f"GBK 系列 CSV {len(g['gbk_encoded_csv'])} 份，扫描件 {len(g['scanned'])} 份）")
    qt: dict[str, int] = {}
    for c in dataset["test_cases"]:
        t = c["annotation"]["query_type"]
        qt[t] = qt.get(t, 0) + 1
    print(f"rag_100_docs.json 校验通过并写入（{len(CASES)} 条用例，"
          f"{len(manifest['documents'])} 份文档）")
    print(f"格式覆盖 {manifest['format_counts']}")
    print(f"query_type 分布 {qt}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
