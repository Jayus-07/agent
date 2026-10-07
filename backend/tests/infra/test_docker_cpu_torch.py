import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]



def test_docker_builder_pins_cpu_torch_before_project_dependencies():
    """构建依赖层先安装 CPU Torch，并约束后续解析不切回 CUDA 版。"""
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    normalized_dockerfile = re.sub(
        r"\s+", " ", re.sub(r"\\\s*", " ", dockerfile)
    )
    constraint_path = REPO_ROOT / "constraints" / "torch-cpu.txt"
    assert constraint_path.is_file(), "缺少 CPU Torch 约束文件"
    torch_constraint = constraint_path.read_text(encoding="utf-8").strip()

    cpu_install = (
        "pip install --no-deps --index-url ${TORCH_CPU_INDEX_URL} "
        "-r constraints/torch-cpu.txt"
    )
    project_install = 'pip install -e ".[postgres,ragas]"'

    assert torch_constraint == "torch==2.14.1+cpu"
    assert (
        "COPY constraints/torch-cpu.txt ./constraints/torch-cpu.txt"
        in normalized_dockerfile
    )
    assert cpu_install in normalized_dockerfile
    assert project_install in normalized_dockerfile
    assert "--constraint constraints/torch-cpu.txt" in normalized_dockerfile
    assert normalized_dockerfile.index(cpu_install) < normalized_dockerfile.index(
        project_install
    )
    assert "EXTRA_INDEX_URL" not in normalized_dockerfile
