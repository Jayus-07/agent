"""config/mcp.py — MCP 配置

两个方向：

1. **平台自有 MCP 出口**（mcp_servers/ → /api/mcp REST）：暂无配置项。
2. **外部 MCP server 作为 Tool 数据源**（2026-10-02 拍板的新方向，
   首个消费方 = 12306 车票查询，drfccv/mcp-server-12306，Docker 部署）：
   平台经官方 mcp SDK 的 Streamable HTTP client 消费，配置在此集中。

纪律与地图 provider（config/map.py 的腾讯/高德）一致：
  1. URL 只从环境变量读取，代码内不落业务环境相关的值；
  2. 数据源是**非官方聚合、无 SLA**——开关默认关，关闭时调用方
     明确报「未启用」，不静默、不阻塞主链路；
  3. 该数据源上游声明「仅供学习研究」，不用于商用场景。
"""
import os


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


# =============================================
# 外部 MCP 数据源：12306 车票查询（drfccv/mcp-server-12306）
# =============================================
# 宿主机直连用 http://127.0.0.1:18000/mcp；app 容器内走 compose
# 服务名 http://mcp-12306:8000/mcp（见 docker-compose.yml）。
TRAIN_MCP_ENABLED = _bool("TRAIN_MCP_ENABLED", False)
TRAIN_MCP_BASE_URL = os.getenv("TRAIN_MCP_BASE_URL",
                               "http://127.0.0.1:18000/mcp").strip()
# 单次调用总超时（秒）：覆盖 initialize 握手 + tools/call 全程
TRAIN_MCP_TIMEOUT = float(os.getenv("TRAIN_MCP_TIMEOUT", "15"))
# 结果缓存秒数：余票分钟级变化即可接受，缓存主要为省上游与防限流
TRAIN_MCP_CACHE_TTL = float(os.getenv("TRAIN_MCP_CACHE_TTL", "120"))
TRAIN_MCP_CACHE_MAXSIZE = int(os.getenv("TRAIN_MCP_CACHE_MAXSIZE", "256"))
# 客户端节流：12306 聚合接口怕突发，保底最小间隔（秒）
TRAIN_MCP_MIN_INTERVAL = float(os.getenv("TRAIN_MCP_MIN_INTERVAL", "0.5"))


def is_train_mcp_enabled() -> bool:
    """外部 12306 数据源是否参与（开关 + URL 非空）。"""
    return bool(TRAIN_MCP_ENABLED and TRAIN_MCP_BASE_URL)
