"""防止新增未审查的 Redis 整库清理与直连调用点。"""

import ast
from pathlib import Path


ALLOWED = {
    'fixtures/redis_test.py',
    'test_task_admission.py',
    'test_task_admission_runtime.py',
    'test_stop_g4_matrix.py',
    'test_distributed_gate.py',
    'test_tool_stats_aggregation.py',
}


def test_redis_test_hygiene():
    root = Path(__file__).resolve().parents[1]
    violations = set()
    for path in root.rglob('*.py'):
        tree = ast.parse(path.read_text(encoding='utf-8-sig'))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute):
                continue
            direct = (
                func.attr == 'from_url'
                and isinstance(func.value, ast.Attribute)
                and func.value.attr == 'Redis'
            )
            if direct or func.attr == 'flushdb':
                relative = path.relative_to(root).as_posix()
                if relative not in ALLOWED:
                    violations.add(relative)
    assert not violations, f'未经分片审查的 Redis 调用：{sorted(violations)}'
