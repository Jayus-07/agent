# 腾讯位置服务（腾讯地图 LBS）接入说明

Key 已配置在服务端 `agent/.env` 的 `TENCENT_LBS_KEY`，代码中不出现任何密钥字面量。
平台能力清单与申请入口见 <https://lbs.qq.com/>。

---

## 1. 已接入的能力

全部走 **WebService API**（HTTPS + JSON），Key 由服务端注入。

| 能力 | 端点 | 代码入口 |
|---|---|---|
| IP 定位（市级） | `/ws/location/v1/ip` | `api.ip_location()` |
| 地理编码（地址→坐标） | `/ws/geocoder/v1/` | `api.geocode()` |
| 逆地理编码（坐标→地址，可带周边 POI） | `/ws/geocoder/v1/` | `api.reverse_geocode()` |
| 地点检索（城市限定 / 周边） | `/ws/place/v1/search` | `api.place_search()` |
| 关键词输入提示 | `/ws/place/v1/suggestion` | `api.place_suggestion()` |
| 行政区划（检索 / 下钻 / 省级列表） | `/ws/district/v1/{search,getchildren,list}` | `api.district_*` |
| 路线规划（驾车/步行/骑行/公交） | `/ws/direction/v1/{mode}/` | `api.direction()` |
| 距离矩阵（N×M 一次算完） | `/ws/distance/v1/matrix` | `api.distance_matrix()` |
| 坐标换算（服务端版） | `/ws/coord/v1/translate` | `api.coord_translate()` |
| 静态地图图片 | `/ws/staticmap/v2/` | `api.static_map_bytes()` |
| **天气**（实时/未来/逐小时） | `/ws/weather/v1/` | `api.weather()` |
| **街景全景点** | `/ws/streetview/v1/getpano` | `api.street_view_pano()` ⚠ 待开通 |
| **街景图片** | `/ws/streetview/v1/image` | `api.street_view_image_bytes()` ⚠ 待开通 |
| **地图调起（导航）** | `/uri/v1/routeplan`、`/uri/v1/marker` | `api.navigation_uri()` |

### 开通状态一览（实测于本 Key）

| 服务 | 状态 | 说明 |
|---|---|---|
| 天气 | ✅ 可用 | 创建 Key 时勾选即可，无需额外申请 |
| 街景 | ⚠ 未开通（返回 113） | **申请制**，见 §3.3 |
| 导航调起 | ✅ 可用 | URI API，无需额外开通 |
| 路线/检索/行政区划/静态图 | ✅ 可用 | — |

一条命令看全部：`GET /map/services`（会真实发起请求探活）。

---

## 2. 分层结构

```
tools/map/*, app/api/routes/maps.py      业务面：给 Agent 的工具 / 给前端的 HTTP 接口
        │
infra/lbs/api.py                          能力门面：把腾讯各异的结构归一为稳定契约
        │
infra/http/tencent_lbs.py                 传输层：鉴权 / 重试 / 缓存 / 限流 / 状态码映射
        │
   apis.map.qq.com
```

- `infra/lbs/geo.py` — 坐标系换算（WGS-84 / GCJ-02 / BD-09），纯函数、离线可测
- `config/map.py` — 全部可调项，均支持环境变量覆盖

---

## 3. 使用方式

### 3.1 后端 HTTP 接口（前端调用这条）

后端前缀 `/map`，前端经 Next.js 重写访问 `/api/map/*`。

```bash
curl "http://localhost:8000/map/health"          # 配置自检
curl "http://localhost:8000/map/services"        # 各附加服务探活（真实请求）
curl "http://localhost:8000/map/geocode?address=福州市鼓楼区南后街139号&city=福州"
curl "http://localhost:8000/map/place/search?keyword=美食&near=26.0824,119.2968&radius=2000"
curl "http://localhost:8000/map/route?from=26.0824,119.2968&to=26.049,119.3896&mode=driving"
curl "http://localhost:8000/map/weather?city=福州&kind=now"          # 天气
curl "http://localhost:8000/map/navigate?to=26.049,119.3896&to_name=鼓山"  # 导航调起
curl "http://localhost:8000/map/static-map?center=26.0824,119.2968&zoom=14&markers=26.0824,119.2968,A" -o map.png
curl "http://localhost:8000/map/street-view/pano?location=26.0824,119.2968"  # 街景（未开通时 502 + 申请指引）
```

**演示台**（无需前端密钥，同源托管避免 CORS）：

```
http://localhost:8000/map/demo
```

### 3.2 Agent 工具（13 个，已注册进 tool_registry）

```
map_geocode_tool           地址 → 坐标
map_reverse_geocode_tool   坐标 → 地址（可选周边 POI）
map_ip_location_tool       IP → 城市
map_district_tool          行政区划检索 / 下钻
map_coord_convert_tool     坐标系换算（本地纯函数，不耗配额）
map_place_search_tool      地点检索（城市限定 / 周边）
map_place_suggest_tool     关键词联想
map_route_tool             路线规划
map_distance_matrix_tool   距离矩阵
map_navigation_tool        导航调起（生成腾讯地图 App 链接）
map_weather_tool           天气（实时 / 未来 / 逐小时）
map_street_view_tool       街景全景图（返回代理地址，未开通时透出申请指引）
map_static_map_tool        静态图（返回后端代理地址，不含密钥）
```

工具失败时返回 `{"error": ...}` 而非空值 —— 让模型能区分「查不到」与「查不了」。

### 3.3 旅行域接入

由 `TRAVEL_USE_LIVE_MAP` 控制（默认在 `agent/.env` 中已开启），接线点在
`backend/travel/register.py` 的 `install_live_map()`。

1. **通勤时长**：`tools/travel/live_map.live_leg` 作为 `routing` 的数据源，
   把「直线距离 × 1.35 ÷ 22km/h」换成腾讯真实路径与路况。
   排程专家、校验器、行程单均无需改动。
2. **必去地点补全**：用户点名但本地种子池没有的地点，用腾讯 POI 库解析成
   真实坐标后补入候选池，`Poi.source = "tencent:lbs"`。

降级保证：数据源异常或查不到时自动回落本地估算，**不会让行程生成失败**。

---

## 4. 天气 / 街景 / 导航：三个附加能力怎么接

### 4.1 天气 —— 直接可用，入参选坐标更准

三种查询：`kind=now`（实时）/ `future`（未来几天，含昼夜）/ `hours`（逐小时 24 条）。

实测到一个选择入参的依据：

```
adcode=350100  → district 为空（只到市级）
location=26.0824,119.2968 → district=鼓楼区 adcode=350102（到区县级）
```

所以 `api.weather_for_city()` 会先把城市名解析成行政区中心点、再用坐标查一次，
而不是直接把城市名当 adcode 用。

另一个坑：`now` 的 `infos` 是**字典**，`future` 的 `infos` 是**列表** ——
同名字段两种类型，不区分处理会直接报错。

### 4.2 街景 —— 申请制，不是控制台里的勾选项

本 Key 请求街景返回 `113 此功能未被授权`。注意这个码的含义：**113 而非 404，
说明端点路径是对的**，只是服务没开通（对比静态图用错版本时返回的是
「错误的请求路径」）。

街景 **仅对企业开发者开放**，且是**邮件申请制**：按官方配额申请模板
（邮件正文格式）发至 `mapapi@vip.qq.com` 并抄送 `mapbd@tencent.com`，
约 3 个工作日审批。在控制台里找那个开关是找不到的。

代码侧已就绪，并做了一处刻意的设计选择：

- 端点与解析已实现（`/ws/streetview/v1/getpano`、`/ws/streetview/v1/image`）
- **街景失败不吞异常**（与其他接口返回 `None` 的降级策略相反）。原因：失败
  成因是「需要用户去申请开通」，静默返回 `None` 会被理解成「这个坐标没有街景」。
  异常消息里直接带申请邮箱，由 `tencent_lbs.apply_hint_for()` 注入。
- 响应字段解析基于官方文档而非实测（服务未开通），因此采用宽松提取 + 原始
  payload 透传，不硬编码字段名。开通后跑自检脚本第 13 节即可核实并收紧。

### 4.3 导航 —— 先说结论：Web 端没有导航 SDK

腾讯位置服务的导航能力只有 **Android 导航SDK / iOS 导航SDK**（原生库）。
Web 端提供的是 JS API、WebService、轨迹云、地点云、URI API（地图调起）——
没有浏览器里的导航 SDK。

网页端想要「真导航」（语音播报、实时偏航重算），三条路：

| 方案 | 能做什么 | 代价 |
|---|---|---|
| **URI API 调起**（已实现） | 唤起腾讯地图 App，由 App 完成全程导航 | 用户跳出你的页面 |
| GL JS + `/direction` 的 steps 自绘 | 页内显示路线与转向列表 | 无语音播报、无偏航重算，做不了车道级引导 |
| 原生 App 集成导航SDK | 完整体验 | 只能在 Android/iOS 客户端做，需单独配包名/bundleId |

`map_navigation_tool` / `GET /map/navigate` 走的是第一条。已验证可用
（HTTP 302 + referer 必填 + 支持驾车策略）。

**这里最关键的是 Key 处理。** 实测 URI API 的 302 跳转会把传入的 Key
**原样带到跳转目标 URL 上**，所以后端代理也藏不住 Key。唯一的解法是
**拆两个 Key**：

- `TENCENT_LBS_KEY`（后端）：WebService 类能力，永不出后端
- `TENCENT_LBS_FRONTEND_KEY`（前端）：只给地图调起与 JS API 用，
  在腾讯控制台给它配 **Referer 域名白名单**

未配前端 Key 时，`/map/navigate` 会回退用后端 Key，并在响应里带
`key_kind: "backend"` + `warning`，明确提示该 URL 不可外发 —— 静默返回
等于把后端日配额交给浏览器。这个判断也在自检脚本里被断言。

## 5. 三个必须知道的坑（都已处理）

### 4.1 单位不一致

| 端点 | `duration` 单位 |
|---|---|
| `/ws/direction/v1/*` | **分钟** |
| `/ws/distance/v1/matrix` | **秒** |

同一段路两个接口返回的数值差了 60 倍。本层统一归一为 `duration_min` 与
`duration_s` 同时给出。自检脚本会交叉比对两个接口的结果，写反了会立刻报错。

### 4.2 行政区划的三个陷阱

- `/ws/district/v1/list` **不支持关键词过滤**，传 `keyword` 会被忽略并返回
  3621 条（34 省 / 493 市 / 3094 区县，分 3 组）。要按名称查请用
  `/ws/district/v1/search`。
- `/ws/district/v1/getchildren` 与 `list` 的第 2、3 组**只返回 `fullname`，
  不返回 `name`**（`{"id":"350102","fullname":"鼓楼区"}`）。直接读 name 会得到空串。
- 静态图端点是 **`v2`** 且**必须带尾斜杠**：`/ws/staticmap/v2/`。用 `v1` 会返回
  「错误的请求路径」。

### 4.3 坐标系

腾讯全线使用 **GCJ-02**。WGS-84（GPS 原始）直接喂进来会有 100~700 米偏移；
百度 BD-09 混用同样出错。`infra/lbs/geo.py` 提供本地换算（已与服务端
`/coord/translate` 逐位对齐，偏差 0.0m）。另外腾讯要求「**纬度,经度**」，
顺序写反不会报错、只会把点画到几百公里外，`geo.parse_lat_lng` 会自动纠正。

---

## 6. 安全与合规

- **Key 不下发前端**。所有 WebService 调用由 `/map/*` 代理，Key 只存在于
  服务端 `.env`。演示台与静态图都不需要前端持有 Key。
- **若要在浏览器直接渲染交互式地图**（GL JS）：需在腾讯控制台为该 Key 配置
  Referer 域名白名单；商用场景应改用服务端密钥代理模式。
- **配额保护**：客户端限流 5 QPS + TTL 缓存；配额类错误（120/121/122/123）
  重试无意义，直接抛出并映射为 HTTP 429。
- **地图数据合规**：仅使用腾讯位置服务等具备资质的国内地图服务，不得使用
  Google / Apple / OSM 直连瓦片等境外或未授权地图源。涉及国界、台湾、南海
  诸岛的展示须符合国家标准。
- **个人信息**：IP 定位精度仅到市级，不得用于推断个人具体位置；批量点位数据
  需遵守《个人信息保护法》。

---

## 7. 自检与测试

```bash
# 实网端到端自检（15 组，覆盖全部能力 + 单位口径交叉验证 + 代理路由 + 密钥不外泄）
.venv/Scripts/python.exe backend/scripts/verify_tencent_lbs.py

# 离线单测（不联网，验证解析/归一/降级）
.venv/Scripts/python.exe -m pytest backend/tests/test_tencent_lbs.py -q
```

`backend/tests/conftest.py` 中有 autouse fixture 在测试期切断 LBS，
避免单测随开发机 `.env` 状态与网络波动而 flaky。

自检脚本里有两段是「预期失败」而非缺陷，会以 `[WARN]` 呈现：
街景未开通（113）与未配置前端 Key。它们断言的是**提示是否到位**，
而不是「服务必须可用」—— 因为这两件事都需要去腾讯侧操作，不是代码能解决的。

---

## 8. 排障

| 现象 | 原因 | 处理 |
|---|---|---|
| 所有接口 503 | 未配置 Key | 检查 `.env` 的 `TENCENT_LBS_KEY`，重启服务 |
| HTTP 429 / 错误码 120-123 | 配额或频率受限 | 降 `TENCENT_LBS_MIN_INTERVAL` 调用频率，或申请企业配额 |
| 错误码 110/112 | Referer/IP 白名单限制 | 腾讯控制台检查该 Key 的白名单配置 |
| 错误码 111 | 开启了 SN 签名校验 | 把 SK 填入 `TENCENT_LBS_SK` |
| 错误码 113 | 分两种：控制台没勾服务，或该服务是申请制 | 先排除申请制（街景即属此类，见 §4.2） |
| 街景 113 | 街景是**邮件申请制**，控制台里没有这个开关 | 见 §4.2 的申请邮箱 |
| 街景图片 502 | 服务未开通，或该全景点已失效 | 先确保 113 已解决 |
| 天气 `district` 为空 | 用了 `adcode` 入参，只到市级 | 改传坐标（`location`） |
| 天气解析报类型错 | `future` 的 `infos` 是列表，`now` 的是字典 | 按 kind 分别处理 |
| 导航链接里能看到密钥 | 未配 `TENCENT_LBS_FRONTEND_KEY`，回退用了后端 Key | 配前端专用 Key + Referer 白名单（§4.3） |
| 调起后落到网页而非 App | 需在手机浏览器中打开；且 `referer` 必填 | 手机端测试 |
| 静态图 404「错误的请求路径」 | 用了 v1 或缺尾斜杠 | 用 `/ws/staticmap/v2/` |
| 点位整体偏移数百米 | 坐标系不匹配 | 用 `map_coord_convert_tool` 转到 GCJ-02 |
| 检索结果跨省 | 未限定城市 | 传 `city` 或 `near` |

---

## 9. 终端用户能用到的范围（接线现状）

**核心事实：工具接好了 ≠ 用户用得上。** 平台的调用链是

```
Planner → Capability（能力名）→ Skill → Tool → 基础设施
```

Planner **只认能力名**，而能力名由 Skill 实例派生。`backend/tools/tool_registry`
是「重复定义检测 + 元数据登记」用的，**生产链路里没有任何代码读它**（只有测试在读）。
因此往 `tools/map/` 里加工具、注册得再整齐，对话里也永远不会被调用 ——
不报错、不告警。

自查当前可见能力：

```bash
.venv/Scripts/python.exe -c "
import backend.skills
from backend.orchestration.tool_registry import tool_registry
print(sorted(tool_registry.CAPABILITY_MAP))"
```

### 现状（TRAVEL_ENABLED=true）

| 用户这样说 | 触发路径 | 结果 |
|---|---|---|
| 「帮我规划福州三日游，2 个人，想去平潭岛和三坊七巷」 | Router 预过滤 → travel 域图 | ✅ 完整行程单 |
| 「福州有哪些人文景点」 | Skill `travel.poi_search` | ✅ 候选 POI（仅 本地种子数据，3 城） |
| 「福州明天天气怎么样」 | — | ❌ Planner 看不到 `map_weather_tool` |
| 「三坊七巷附近有什么好吃的」 | — | ❌ `map_place_search_tool` 不可达 |
| 「从这儿怎么去鼓山」 | — | ❌ `map_route_tool` / `map_navigation_tool` 不可达 |
| 「给我看一眼那个路口的街景」 | — | ❌ `map_street_view_tool` 不可达 |
| 「画一张带标注的地图」 | — | ❌ `map_static_map_tool` 不可达 |

行程单里**已经**是真实数据：通勤时长/费用走腾讯路径规划（`TransitLeg.source = tencent:lbs`），
用户点名但本地没有的地点用腾讯 POI 补全坐标后入池。

### 要接通地图类能力需要做什么

包 1 个聚合 Skill（例如能力名 `map.lookup`），内部按意图把参数分派到 13 个工具之一。
不要给 13 个工具各包一个 Skill —— Planner 的 prompt 每轮会多 13 段能力描述，
而能力名越多、误选概率越高。

注册 Skill 后务必跑一次上面的 `CAPABILITY_MAP` 自查命令确认能力名真的出现了。

### ⚠ 开启 `TRAVEL_ENABLED` 的副作用

`travel/graph_builder._build_checkpointer()` 在 `TRAVEL_ENABLED=true` 时会启用
`MemorySaver` —— **纯内存、无 TTL、无上限**。开发/联调无碍，长期运行的服务器会
随会话数缓慢增长。原设计的三个待办（`TRAVEL_CHECKPOINTER_ENABLED` 开关 /
Postgres 后端 / TTL 清理）尚未落地，上生产前需要补。

另外：`<DOMAIN>_ENABLED` 从 `.env` 读取，本地一开就可能让一批「直连域图、
不传 thread_id」的测试失败（checkpointer 生效后该键变必填）。改完 `.env`
务必跑一次全量 `backend/tests`，别只看本域的用例。
