import re
from collections import Counter

path = r"D:/Program Files/workplace/agent/tmp/full_run_summary.txt"
fails = Counter()
errs = Counter()
with open(path, encoding="utf-8", errors="replace") as f:
    for line in f:
        m = re.match(r"^(FAILED|ERROR) (backend/tests/[^:]+\.py)", line.strip())
        if not m:
            continue
        kind, fp = m.group(1), m.group(2)
        (fails if kind == "FAILED" else errs)[fp] += 1

print("=== FAILED by file ===")
for fp, n in fails.most_common():
    print(f"{n:4d}  {fp}")
print(f"total failed: {sum(fails.values())} in {len(fails)} files")
print()
print("=== ERROR by file ===")
for fp, n in errs.most_common():
    print(f"{n:4d}  {fp}")
print(f"total errors: {sum(errs.values())} in {len(errs)} files")
