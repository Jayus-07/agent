"""modules — 业务模块统一入口（re-export）

按 task 规范，所有业务模块应在 modules/ 下统一管理。
当前架构已满足"按职责清晰划分"，本目录作为统一对外入口，
避免破坏现有 import 路径（不重建抽象层）。

包含:
  - skills/  业务流程（re-export from backend.agent.skills）
  - tools/   工具接口（re-export from backend.agent.tools）

新增业务模块时，新建对应子目录 + 在此 __init__ 暴露。
"""