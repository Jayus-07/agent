"""D0 侦察脚本：rollinggo live 低价酒店样本实测（travel-value-showcase）。

用途：通过 `travel_v2.services.hotel_search_service.search_hotel_offers`（off/fake/live
三态收编件唯一入口）拉取目标城市真实酒店报价，供平价档定额定稿依据。
只读查询，不改任何状态。

注意：本地进程需先刷新 MCP 注册表快照（应用启动时才会拉取），否则 live
解析返回 disabled。金额字段是 service 层映射后的 `price_total`（字符串语义）。
复跑：worktree 根目录执行
  ".venv/Scripts/python.exe" -I docs/tasks/travel-value-showcase/d0-hotel-sample.py
"""
import asyncio
import json
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.getcwd())

from dotenv import load_dotenv

load_dotenv(".env")

from backend.infra.mcp_registry import store as mcp_store

asyncio.run(mcp_store.refresh_now())  # 本地进程先拉一次注册表快照（应用启动时才刷新）

from backend.travel_v2.services import hotel_search_service as hs

CITIES = ["福州", "厦门", "成都", "西安", "杭州"]
check_in = date.today() + timedelta(days=7)
check_out = check_in + timedelta(days=1)

summary = {}
for city in CITIES:
    try:
        data = hs.search_hotel_offers(
            city=city, check_in=check_in, check_out=check_out,
        )
    except hs.HotelSearchParamError as exc:
        summary[city] = {"status": "param_error", "reason": str(exc)}
        print(f"{city}: PARAM_ERROR {exc}")
        continue
    status = data.get("status")
    rows = []
    for off in data.get("offers") or []:
        amount = off.get("price_total")
        if amount is None:
            continue
        rows.append({
            "name": off.get("property_name"),
            "total": float(amount),
            "nights": off.get("nights"),
            "currency": off.get("currency"),
            "room": off.get("room_type"),
            "avail": off.get("availability"),
            "source": off.get("provider"),
        })
    rows.sort(key=lambda r: r["total"])
    summary[city] = {
        "status": status, "reason": data.get("reason"),
        "count": len(rows), "rows": rows,
    }
    print(f"=== {city} status={status} reason={data.get('reason')} offers={len(rows)} ===")
    for r in rows:
        print(f"  CNY {r['total']:>8.0f}  {r['name']}  avail={r['avail']}")

with open("d:/tmp/d0_hotel_sample_result.json", "w", encoding="utf-8") as f:
    json.dump(summary, f, ensure_ascii=False, indent=2)
print("saved -> d:/tmp/d0_hotel_sample_result.json")
