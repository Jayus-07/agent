# 腾讯位置服务接入 · 最终验收报告

- **验收时间**：2026-09-14 22:36（实网自检）
- **验收方式**：`python backend/scripts/verify_tencent_lbs.py`（实网调用腾讯 LBS 全量自检）+ 单元回归 + e2e 旅行域验证
- **结论**：✅ **验收通过**（60 PASS / 2 WARN / 0 FAIL，两个 WARN 均为预期状态，非缺陷）

---

## 1. 实网自检结果

证据归档：`docs/tencent-lbs-verify-2026-09-14.log`（完整输出）

| 模块 | 结果 | 说明 |
|---|---|---|
| 地理编码 / 逆地理 | PASS | GCJ-02，区县级精度 |
| POI 检索 / 关键词建议 | PASS | |
| 行政区划 | PASS | children 只回 fullname（已文档化） |
| 路线规划 /distance 与 /direction | PASS | duration 单位差异已归一（分 vs 秒） |
| 距离矩阵 | PASS | |
| 静态地图 | PASS | 返回合法 PNG |
| **天气** | PASS | 实时（福州 鼓楼区 多云 26℃）+ 4 天预报 + 24h 逐小时 |
| **街景** | **WARN（预期）** | 错误 113 = 申请制服务，异常消息已含申请邮箱指引（mapapi@vip.qq.com） |
| **导航调起 URI** | PASS + WARN | 链接可生成、策略生效；未配 `TENCENT_LBS_FRONTEND_KEY` 时回退后端 Key 并**显式告警**（不静默泄露） |
| 后端代理路由 `/map/*` | PASS | 10 条路由全部 200/400/502 符合预期；**所有响应均不含密钥** |

## 2. 回归与 e2e

- 聚焦单测（`test_tencent_lbs.py` + `tests/travel`）：**228 passed / 0 failed**
- 全量套件（`TRAVEL_ENABLED=true`，~3300 条，golden set 除外）：**全绿**，回归对比 0 新增失败
- e2e：平潭岛经 LBS 实时解析（`source=tencent:lbs`、`required=True`）并正确进入行程排程

## 3. Key 安全审计

| 检查项 | 结果 |
|---|---|
| 真实 Key 存放位置 | 根目录 `.env` 的 `TENCENT_LBS_KEY`（`load_dotenv()` 加载）|
| `.env` 是否被 git 忽略 | ✅ `git check-ignore` 确认（根 `.env` 与 `backend/.env` 均忽略）|
| git 跟踪文件中是否含真实 Key | ✅ 无（仅注释中的格式示例，已脱敏为「6 段式」描述）|
| 后端代理响应是否泄露 Key | ✅ 自检确认「其余代理响应均不含密钥」|

**遗留建议**：
1. Key 曾在对话中明文出现，建议在腾讯位置服务控制台**更换 Key** 或配置 Referer 白名单；
2. 配置 `TENCENT_LBS_FRONTEND_KEY`（前端专用 Key + 域名白名单）后，导航调起链接才可外发给终端用户；
3. **街景：待企业认证后申请接入**（2026-09-14 确认）。当前账号为**个人开发者**，而街景静态图/场景查询 API **仅对企业开发者开放**，个人身份无法申请；邮件申请通过后（`mapapi@vip.qq.com` 抄送 `mapbd@tencent.com`，3 个工作日审批）即可启用——代码已实现（`backend/infra/lbs/api.py::street_view_pano`，113 异常透出申请指引），**无需改代码**，只需跑 `scripts/verify_tencent_lbs.py` 街景段落核实响应字段并收紧解析。另注意：官方模板 `lbs.qq.com/template.docx` 链接已失效（下载返回官网首页），邮件正文按配额惯例自组织即可。

## 4. 回归判定工具说明

- `scripts/regression_check.sh`：判回归专用脚本。自动跑基线（`TRAVEL_ENABLED=false`）并与当前配置的全量失败比对，**只报「新增失败」**，避免被 golden set 的环境性失败误导。
- `backend/tests/.regression_baseline.txt`：基线清单（当前为 0 条——排除会挂死的 golden set 后全量本就全绿）。
- 用法：`bash scripts/regression_check.sh check`；退出码 `0`=无新增（可放心）/ `1`=有回归 / `2`=用法错误。
- `test_eval_golden.py`（RAG 黄金集评测，需 Postgres+embedding+reranker+LLM）不参与本沙箱比对，应在有真实基础设施的 CI 中单独跑。

## 5. 交付物清单

| 类型 | 文件 |
|---|---|
| 配置 | `backend/config/map.py`（Key 全部从 env 读取）|
| HTTP 客户端 | `backend/infra/http/tencent_lbs.py`（SN 签名 / 缓存 / 限频间隔）|
| API 门面 | `backend/infra/lbs/api.py` |
| 地图工具 | `backend/tools/map/`（13 个 `@tool`）|
| 旅行域适配 | `backend/tools/travel/live_map.py`、`routing.py`、`travel/experts/poi.py`、`travel/reporter.py` |
| 后端代理 | `backend/app/api/routes/maps.py`（`/map/health` 等 10 条路由）|
| 自检脚本 | `backend/scripts/verify_tencent_lbs.py`（60 PASS / 2 WARN / 0 FAIL）|
| 演示台 | `backend/scripts/serve_map_demo.py` → `http://127.0.0.1:8085/map/demo` |
| 单测 | `backend/tests/test_tencent_lbs.py`（119 条）+ travel 域测试 |
| 文档 | `docs/tencent-lbs.md`、`docs/tencent-lbs-demo.html`、本报告、自检日志 |
