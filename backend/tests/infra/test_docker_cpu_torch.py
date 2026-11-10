import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]



def test_docker_builder_pins_cpu_torch_before_project_dependencies():
    """构建依赖层先安装 CPU Torch，并约束后续解析不切回 CUDA 版。

    供应链收口（9a760d4）后依赖安装改为消费 requirements-lock.txt（torch 行
    与 constraints/torch-cpu.txt 同源），pyproject 不再参与构建期解析；本守卫
    2026-11-10 同步到新机制（旧断言仍守卫 pip install -e ".[postgres,ragas]"
    浮动解析串，导致基线恒败）：
    CPU wheel 先装 → lock 安装（torch 由 lock 同版本 pin）→ 项目本体 --no-deps。
    """
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    normalized_dockerfile = re.sub(
        r"\s+", " ", re.sub(r"\\\s*", " ", dockerfile)
    )
    compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    constraint_path = REPO_ROOT / "constraints" / "torch-cpu.txt"
    assert constraint_path.is_file(), "缺少 CPU Torch 约束文件"
    torch_constraint = constraint_path.read_text(encoding="utf-8").strip()
    lock_path = REPO_ROOT / "requirements-lock.txt"
    assert lock_path.is_file(), "缺少 requirements-lock.txt（供应链收口前提）"
    lock_torch_lines = [
        line.strip()
        for line in lock_path.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("torch==")
    ]

    cpu_install = (
        "pip install --no-deps --index-url ${TORCH_CPU_INDEX_URL} "
        "-r constraints/torch-cpu.txt"
    )
    lock_install = "pip install -r requirements-lock.txt"
    project_install = "pip install --no-deps -e ."

    assert torch_constraint == "torch==2.14.1+cpu"
    # lock 的 torch 行必须与 constraints 同源——这是「lock 安装阶段不切回
    # CUDA」的保障（替代旧 --constraint 机制）
    assert lock_torch_lines == [torch_constraint], (
        "requirements-lock.txt 的 torch 必须与 constraints/torch-cpu.txt 同源"
    )
    assert (
        "COPY constraints/torch-cpu.txt ./constraints/torch-cpu.txt"
        in normalized_dockerfile
    )
    assert (
        "COPY requirements-lock.txt ./requirements-lock.txt"
        in normalized_dockerfile
    )
    assert cpu_install in normalized_dockerfile
    assert lock_install in normalized_dockerfile
    assert project_install in normalized_dockerfile
    assert normalized_dockerfile.index(cpu_install) < normalized_dockerfile.index(
        lock_install
    )
    assert normalized_dockerfile.index(lock_install) < normalized_dockerfile.index(
        project_install
    )
    # 追加索引只允许 CPU 官方索引（受 lock 同源 pin 约束），不得引入泛化源
    extra_index_usages = re.findall(
        r"--extra-index-url \S+", normalized_dockerfile
    )
    assert extra_index_usages == ["--extra-index-url ${TORCH_CPU_INDEX_URL}"], (
        f"Dockerfile 出现非 CPU 索引的 extra-index-url: {extra_index_usages}"
    )
    assert "EXTRA_INDEX_URL" not in compose
