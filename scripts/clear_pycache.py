"""清除项目所有 __pycache__ 目录 — 改代码后报错死活不生效时运行此脚本"""
import shutil
import os
import sys

target = os.path.dirname(os.path.abspath(__file__))
count = 0
for root, dirs, files in os.walk(target):
    if '__pycache__' in dirs:
        p = os.path.join(root, '__pycache__')
        shutil.rmtree(p)
        count += 1
        print(f"  removed: {p}")
print(f"\n清除了 {count} 个 __pycache__ 目录")
if count > 0:
    print("请重启后端服务使改动生效")
