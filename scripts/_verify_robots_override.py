"""临时验证脚本：ROBOTS_OVERRIDE=warn_only 下重新提交淘宝商品链接。"""
import backend.config  # noqa: F401  触发根 .env 加载
import os

from backend.competitor.pipeline import analyze_url

URL = (
    "https://item.taobao.com/item.htm?ali_refid=a3_420860_1007%3A26919299%3AH%3A26919299_0_24957898290"
    "%3A1f81231deb8ac4ef8cb9b70ab2b28e37&ali_trackid=319_1f81231deb8ac4ef8cb9b70ab2b28e37"
    "&id=815673415507&item_type=ad&mi_id=0000T1e1xN5HFrrUrrKL_ZieiIZHfNrlx8NfWYXCmL2Zv-o"
    "&mm_sceneid=0_0_26919299_0&spm=tbpc.pc_sem_alimama%2Fa.201876.d2"
)

print(f"ROBOTS_OVERRIDE = {os.getenv('ROBOTS_OVERRIDE')!r}")
result = analyze_url(URL, use_llm=False)
print("=" * 60)
print(result)
