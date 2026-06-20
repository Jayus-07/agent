"""Runner 注册引导 — CLI 启动时自动导入此模块以注册所有内置 runner。

此文件是评估框架与具体项目的对接点。复制框架到新项目后，替换此文件：
1. 导入自己项目的 runner 模块
2. 调用 evaluation.registry.register_runner() 注册

或者通过 CLI --runner-config 标志指定自定义注册脚本：
    python -m evaluation --runner-config myproject.runners_config

运行时行为：
    import evaluation.runners_config
     → 触发 evaluation.runners.builtin 的导入
       → builtin 模块在加载时调用 register_runner() 注册 4 个 runner
"""

# 导入即注册 — 触发 builtin.py 中的 register_runner() 调用
import evaluation.runners.builtin  # noqa: F401 — side-effect import
