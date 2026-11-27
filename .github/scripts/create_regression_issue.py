"""当 full_regression.yml 检测到测试失败时自动创建 GitHub Issue."""

import json
import os
import subprocess
import sys


def main() -> None:
    raw_needs = os.environ.get("NEEDS", "{}")
    try:
        needs = json.loads(raw_needs)
    except Exception:
        needs = {}

    if isinstance(needs, list):
        needs = {job.get("name", "?"): job for job in needs}

    failed_suites = []
    for name, job in sorted(needs.items()):
        result = job.get("result", "")
        if result not in ("success", "skipped"):
            failed_suites.append(f"{name} ({result})")

    if not failed_suites:
        print("所有回归 Suite 均通过，无需创建 Issue。")
        return

    commit_sha = os.environ.get("COMMIT_SHA", "")[:7]
    run_url = os.environ.get("RUN_URL", "")
    title = f"[周回归报警] commit {commit_sha} 测试矩阵发现失败"

    # 查重：若近期已有同名或未关闭的相关 issue，避免重复打扰
    try:
        existing = subprocess.check_output(
            ["gh", "issue", "list", "--state", "open", "--label", "ci-regression", "--json", "title"],
            text=True,
        )
        if title in existing:
            print(f"已有开启的相同 Issue: {title}，跳过重复创建。")
            return
    except Exception as e:
        print(f"查重检查忽略异常: {e}")

    failed_list_md = "\n".join(f"- {s}" for s in failed_suites)
    body = (
        f"### 周回归矩阵检测到测试失败\n\n"
        f"- **触发提交**: `{commit_sha}`\n"
        f"- **Actions 运行链接**: [{run_url}]({run_url})\n\n"
        f"#### 失败/异常 Suite 清单:\n"
        f"{failed_list_md}\n\n"
        f"> 本工单由 `full_regression.yml` 自动巡检创建，请维护团队及时定位排查。"
    )

    cmd = [
        "gh", "issue", "create",
        "--title", title,
        "--body", body,
        "--label", "ci-regression",
    ]
    try:
        subprocess.check_call(cmd)
        print(f"成功创建回归告警 Issue: {title}")
    except Exception as e:
        print(f"创建 Issue 失败: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
