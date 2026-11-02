"""检查全量回归显式路径是否覆盖所有测试顶层目录。"""

import re
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    workflow = (root / '.github/workflows/full_regression.yml').read_text(
        encoding='utf-8'
    )
    # 只读取 paths 字段，避免注释中的目录被误认为覆盖。
    paths = re.findall(
        r'^\s*- \{ name: [\w-]+, paths: "([^"]*)" \}',
        workflow, re.MULTILINE,
    )
    covered = {item for value in paths for item in value.split()}
    actual = {
        f'backend/tests/{item.name}'
        for item in (root / 'backend/tests').iterdir()
        if item.is_dir() and item.name not in {'__pycache__', 'fixtures'}
    }
    missing = sorted(actual - covered)
    if missing:
        raise SystemExit(f'全量 matrix 未覆盖目录：{missing}')
    print(f'matrix 目录覆盖通过：{len(actual)} 个目录')


if __name__ == '__main__':
    main()
