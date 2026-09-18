"""build_review_html.py — 生成人工复核页面（review_67.html）。

把标注复核工作包转成可点击的本地 HTML：19 条仲裁（三方分歧）+ 48 条
快速确认（机器预标默认选中）。复核人逐条定标后点"导出复核结果"下载
review_result.json，交由 apply_review.py 回写生成正式黄金集 v1。

设计约定（规划 §1.1 双人 Kappa 的实现路径）：
  - 第一轮：第一标注员（fixture_seed 之外的人）用本页面定标；
  - 第二轮：第二标注员用同一页面重标（页面顶部填姓名），
    apply_review.py 对两份结果算 Kappa——≥0.80 才达规划门禁。
  - 正文摘录 1500 字（分类判断足够；与线上一致的采样口径）。

用法（仓库根）：
  python -m backend.eval.metadata_baseline.build_review_html
  # 打开 backend/eval/metadata_baseline/review_67.html，复核后导出
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

BASE = Path(__file__).parent
TEXT_EXCERPT = 1500


def _load_items() -> list[dict]:
    from backend.eval.metadata_baseline.build_seed_from_fixtures import extract_text

    pkg = json.loads((BASE / "annotation_workpackage.json").read_text(encoding="utf-8"))
    seed_text: dict[str, dict] = {}
    for line in (BASE / "golden_seed.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            seed_text[row["id"]] = row
    items: list[dict] = []
    for d in pkg["part_a_seed"]["disputes"]:
        row = seed_text.get(d["id"], {})
        items.append({
            "id": d["id"], "source": "part_a_仲裁", "filename": row.get("filename", ""),
            "text_excerpt": (row.get("text", "") or "")[:TEXT_EXCERPT],
            "machine": {
                "seed_gold": d.get("seed_gold"), "rule_pred": d.get("rule_pred"),
                "unified_pred": d.get("unified_pred"),
                "unified_confidence": d.get("unified_confidence"),
                "dispute_pattern": d.get("dispute_pattern"),
            },
            "suggested": None,  # 仲裁条目不给默认值，强制人工判断
        })
    for p in pkg["part_b_detail"]:
        if p.get("grade") != "candidate":
            continue
        fp = Path(p["file_path"]) if p.get("file_path") else None
        text = ""
        if fp and fp.exists():
            try:
                text = extract_text(fp)
            except Exception:
                text = ""
        items.append({
            "id": "skip-" + p["file"], "source": "part_b_快速确认",
            "filename": p["file"], "text_excerpt": text[:TEXT_EXCERPT],
            "machine": {"prelabel": (p.get("prelabel") or {}).get("doc_type"),
                        "prelabel_confidence": (p.get("prelabel") or {}).get("confidence"),
                        "skip_reason": p.get("reason")},
            "suggested": (p.get("prelabel") or {}).get("doc_type"),
        })
    return items


_HTML = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>元数据黄金集复核（67 条）</title>
<style>
  body { font-family: "Microsoft YaHei", sans-serif; max-width: 960px; margin: 24px auto; padding: 0 16px; color: #222; }
  .bar { position: sticky; top: 0; background: #fff; padding: 12px 0; border-bottom: 2px solid #ddd; z-index: 9; }
  .bar input { width: 160px; }
  .progress { display: inline-block; margin-left: 12px; font-weight: bold; }
  button { padding: 8px 16px; cursor: pointer; }
  .card { border: 1px solid #ccc; border-radius: 8px; padding: 14px 16px; margin: 14px 0; }
  .card.done { border-color: #4a4; background: #f4fff4; }
  .card.arbitration { border-left: 5px solid #c60; }
  .card.confirm { border-left: 5px solid #07c; }
  .meta { color: #666; font-size: 13px; margin: 4px 0; }
  .machine { background: #f6f6f6; border-radius: 6px; padding: 8px 10px; font-size: 13px; margin: 8px 0; }
  .machine b { color: #036; }
  pre.text { white-space: pre-wrap; background: #fafafa; padding: 10px; border-radius: 6px; font-size: 13px; max-height: 260px; overflow-y: auto; }
  .types { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
  .types label { border: 1px solid #bbb; border-radius: 14px; padding: 3px 12px; cursor: pointer; font-size: 13px; }
  .types label.sel { background: #036; color: #fff; border-color: #036; }
  .types input { display: none; }
</style>
</head>
<body>
<div class="bar">
  复核人姓名：<input id="reviewer" placeholder="必填，导出用">
  <span class="progress" id="prog">0 / __TOTAL__</span>
  <button onclick="export_result()" style="float:right">导出复核结果</button>
</div>
<div id="cards"></div>
<script>
const ITEMS = __ITEMS__;
const TYPES = ["listing","sop","ad_policy","faq","product_spec","training","policy",
  "compliance","legal","security","financial","customer_data","contract_template","general"];
const chosen = {};
const el = document.getElementById("cards");

function machine_html(m) {
  const parts = [];
  if (m.seed_gold) parts.push("种子标签: <b>" + m.seed_gold + "</b>");
  if (m.rule_pred) parts.push("规则链: <b>" + m.rule_pred + "</b>");
  if (m.unified_pred) parts.push("统一抽取: <b>" + m.unified_pred + "</b> (conf " + m.unified_confidence + ")");
  if (m.prelabel) parts.push("LLM 预标: <b>" + m.prelabel + "</b> (conf " + m.prelabel_confidence + ")");
  if (m.dispute_pattern) parts.push("分歧模式: " + m.dispute_pattern);
  if (m.skip_reason) parts.push("被跳过原因: " + m.skip_reason);
  return parts.join("　|　");
}

function render(item, idx) {
  const div = document.createElement("div");
  div.className = "card " + (item.source.startsWith("part_a") ? "arbitration" : "confirm");
  div.id = "c" + idx;
  const types = TYPES.map(t =>
    "<label data-t='" + t + "'>" + t + "</label>").join("");
  div.innerHTML = "<h3>" + (idx + 1) + ". " + item.filename + " <small>[" + item.source + "]</small></h3>"
    + "<div class='meta'>" + item.id + "</div>"
    + "<div class='machine'>" + machine_html(item.machine) + "</div>"
    + "<pre class='text'>" + item.text_excerpt.replace(/</g, "&lt;") + "</pre>"
    + "<div class='types' data-idx='" + idx + "'>" + types + "</div>";
  el.appendChild(div);
  if (item.suggested) pick(idx, item.suggested);
}

// 事件委托：label 点击 → 定标（避免内联 onclick 的引号嵌套——曾致整页 JS 语法错误）
el.addEventListener("click", function (ev) {
  const t = ev.target && ev.target.dataset && ev.target.dataset.t;
  if (!t) return;
  const box = ev.target.closest(".types");
  if (!box) return;
  pick(parseInt(box.dataset.idx, 10), t);
});

function pick(idx, t) {
  chosen[idx] = t;
  const labels = document.querySelectorAll("#c" + idx + " label");
  labels.forEach(l => l.classList.remove("sel"));
  document.getElementById("l" + idx + "_" + t).classList.add("sel");
  document.getElementById("c" + idx).classList.add("done");
  document.getElementById("prog").textContent = Object.keys(chosen).length + " / " + ITEMS.length;
}

function export_result() {
  const reviewer = document.getElementById("reviewer").value.trim();
  if (!reviewer) { alert("请先填写复核人姓名"); return; }
  if (Object.keys(chosen).length < ITEMS.length) {
    if (!confirm("还有 " + (ITEMS.length - Object.keys(chosen).length) + " 条未定标，确定只导出已定标部分？")) return;
  }
  const out = { reviewer: reviewer, exported_at: new Date().toISOString(),
                decisions: Object.entries(chosen).map(([i, t]) => ({
                  id: ITEMS[i].id, source: ITEMS[i].source, filename: ITEMS[i].filename,
                  machine_suggested: ITEMS[i].suggested || ITEMS[i].machine.prelabel || ITEMS[i].machine.seed_gold,
                  final_doc_type: t, agreed_with_machine: t === (ITEMS[i].suggested || ITEMS[i].machine.prelabel || ITEMS[i].machine.seed_gold) })) };
  const blob = new Blob([JSON.stringify(out, null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "review_result_" + reviewer + ".json";
  a.click();
}
ITEMS.forEach(render);
</script>
</body>
</html>
"""


def main(argv: list[str] | None = None) -> int:
    items = _load_items()
    html = _HTML.replace("__TOTAL__", str(len(items))).replace(
        "__ITEMS__", json.dumps(items, ensure_ascii=False))
    out = BASE / "review_67.html"
    out.write_text(html, encoding="utf-8")
    print(f"复核页面: {out}（{len(items)} 条，浏览器打开 → 填姓名 → 逐条定标 → 导出）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
