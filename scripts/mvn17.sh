#!/usr/bin/env bash
# 用 JDK 17 运行 Maven（仅当前进程生效，不修改系统 JAVA_HOME）
# 用法示例：
#   ./scripts/mvn17.sh business-service package
#   ./scripts/mvn17.sh api-gateway clean package
#   ./scripts/mvn17.sh business-service test
set -euo pipefail

PROJECT="${1:?用法: mvn17.sh <business-service|api-gateway> <mvn 参数...>}"
shift

# JDK 17 安装目录（并存安装，系统默认仍是 JDK 8）
JDK17="$(ls -d "/d/Program Files/Java/"jdk-17* 2>/dev/null | head -1 || true)"
if [[ -z "$JDK17" ]]; then
    echo "未找到 JDK 17，请确认已解压到 D:\\Program Files\\Java\\jdk-17*" >&2
    exit 1
fi

export JAVA_HOME="$JDK17"
export PATH="$JAVA_HOME/bin:$PATH"

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)/$PROJECT"
if [[ ! -f "$PROJECT_DIR/pom.xml" ]]; then
    echo "目录 $PROJECT_DIR 下没有 pom.xml" >&2
    exit 1
fi

echo "==> JAVA_HOME = $JAVA_HOME"
cd "$PROJECT_DIR"
exec mvn "$@"
