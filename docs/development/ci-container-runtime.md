# CI 容器基础运行时架构（GHCR CI Base Image）

## 背景与演进目标

在 GitHub Actions CI 中，后端与 RAG 定向 Smoke 测试（如 `.github/workflows/rag_smoke.yml`）依赖庞大的 Python 工具链（包含 PyTorch CPU、Transformers、LangChain、pgvector 等上百个锁定的包）。每次运行通过 `pip install -r requirements-lock.txt` 动态下载与解压安装，耗时约 3~5 分钟。

为将 PR 单元测试准备耗时由 **5 分钟压缩至 1 分钟内**，项目引入基于 GitHub Container Registry (GHCR) 的预打包 CI 基础镜像方案。

## 架构组成

1. **Dockerfile.ci**
   - 基础系统镜像：`python:3.10-slim`
   - 系统工具：`build-essential`, `curl`, `git`, `libpq-dev`, `postgresql-client`
   - Python 环境：预先执行 `pip install -r requirements-lock.txt --extra-index-url https://download.pytorch.org/whl/cpu`
   - 产物：开箱即用的测试运行时，包含项目全部锁定的运行时与测试依赖。

2. **自动构建流水线（.github/workflows/build-ci-image.yml）**
   - 触发时机：
     - 当 `Dockerfile.ci` 或 `requirements-lock.txt` 发生变更推送到 `main` 时自动触发；
     - 支持通过 GitHub Actions 控制台手动 `workflow_dispatch` 触发。
   - 产出镜像目标：
     - `ghcr.io/jayus-07/agent/ci-base:latest`
     - `ghcr.io/jayus-07/agent/ci-base:<commit-sha>`

3. **CI 工作流接入方式（Container Job）**
   在 GitHub Actions 工作流中，通过指定 `container` 即可秒级进入预置环境，跳过 Python setup 与 pip install 耗时：

   ```yaml
   jobs:
     test:
       runs-on: ubuntu-latest
       container:
         image: ghcr.io/jayus-07/agent/ci-base:latest
       steps:
         - uses: actions/checkout@v4
         - name: Run targeted tests
           run: |
             python -m pytest backend/tests/api/test_auth_sso.py
   ```
