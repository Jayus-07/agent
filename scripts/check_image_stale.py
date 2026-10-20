#!/usr/bin/env python
"""check_image_stale.py — 判断 Docker 镜像是否落后于源代码

被 start.bat（网关模式）调用，决定 `docker compose up -d` 是否需要加 `--build`。

判定逻辑：取源代码目录/文件中「最新的 mtime」，与 compose 服务 app 所用镜像的
「构建完成时间」比较；源代码比镜像新 → 视为过期。

退出码：
  0 = 镜像是最新的（或镜像尚不存在 / 无可比对文件，up -d 会自动构建，无需 --build）
  1 = 镜像过期，需要 --build
  2 = 检测过程出错（docker 不可用等）——bat 侧 errorlevel>=1 同样按需要构建处理（fail-safe）
"""

import datetime
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]

# 参与比对的源代码范围：三个 Python 服务的 COPY 来源 + 两个 Java 工程源码
# （app / rag-service / mcp-service 共用同一镜像，COPY backend/ mcp_servers/ scripts/）
SCAN_DIRS = [
    "backend",
    "mcp_servers",
    "scripts",
    os.path.join("api-gateway", "src"),
    os.path.join("business-service", "src"),
]
SCAN_FILES = [
    "Dockerfile",
    "pyproject.toml",
    "requirements-lock.txt",
    os.path.join("api-gateway", "Dockerfile"),
    os.path.join("business-service", "Dockerfile"),
]

# 排除项：运行时数据（backend/backend/data 等路径含 data 组件）、缓存、构建产物。
# 注意：按路径组件名排除，任何层级出现同名目录/文件均跳过——
# 换取的语义是「源代码变更才触发重建」，运行时写入不误报。
EXCLUDE_PARTS = {
    "__pycache__", ".git", ".pytest_cache", ".ruff_cache", ".mypy_cache",
    "data", "logs", "log", "htmlcov", "node_modules", "target",
    ".venv", "coverage", "dist", "build",
}
EXCLUDE_EXTS = {".pyc", ".pyo", ".log", ".bin", ".class", ".jar", ".idx"}

# 容忍度（秒）：镜像构建完成时间一般晚于被 COPY 文件的 mtime，
# 文件系统时间戳精度/时钟偏差留 60s 余量，避免临界误报
TOLERANCE_SECONDS = 60


def newest_source_mtime() -> float:
    """扫描范围内最新文件的 mtime；无可比对文件返回 0"""
    newest = 0.0
    for rel in SCAN_DIRS + SCAN_FILES:
        path = ROOT / rel
        if path.is_file():
            newest = max(newest, path.stat().st_mtime)
            continue
        if not path.is_dir():
            continue
        for f in path.rglob("*"):
            if not f.is_file():
                continue
            if any(part in EXCLUDE_PARTS for part in f.parts):
                continue
            if f.suffix.lower() in EXCLUDE_EXTS:
                continue
            try:
                newest = max(newest, f.stat().st_mtime)
            except OSError:
                continue
    return newest


def image_created_epoch() -> float | None:
    """compose 服务 app 所用镜像的构建完成时间（epoch 秒）；找不到镜像返回 None

    注意不用 `docker compose images`：容器运行中重建过镜像后，它可能返回
    已被覆盖的旧 sha（实测报 No such image）。compose 构建的镜像带
    com.docker.compose.* label，按 label 过滤最可靠。
    """
    r = subprocess.run(
        ["docker", "image", "ls", "-q",
         "--filter", "label=com.docker.compose.service=app"],
        capture_output=True, text=True, cwd=ROOT,
    )
    image_ids = r.stdout.split()
    if not image_ids:
        return None
    r2 = subprocess.run(
        ["docker", "image", "inspect", "-f", "{{.Created}}", image_ids[0]],
        capture_output=True, text=True,
    )
    if r2.returncode != 0:
        return None
    created = r2.stdout.strip()
    # 形如 2026-09-13T02:00:00.123456789Z（纳秒精度，fromisoformat 不认）→ 截到微秒
    if "." in created and created.endswith("Z"):
        head, tail = created[:-1].split(".", 1)
        created = f"{head}.{tail[:6]}Z"
    dt = datetime.datetime.fromisoformat(created.replace("Z", "+00:00"))
    return dt.timestamp()


def main() -> int:
    newest = newest_source_mtime()
    if newest == 0:
        print("FRESH: no source files found")
        return 0

    created = image_created_epoch()
    if created is None:
        # 镜像尚不存在：compose up -d 会自动构建，无需显式 --build
        print("FRESH: image not built yet (compose up will build)")
        return 0

    if newest > created + TOLERANCE_SECONDS:
        age = newest - created
        print(f"STALE: source changed {age:.0f}s after image build")
        return 1

    print("FRESH: image newer than all source files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
