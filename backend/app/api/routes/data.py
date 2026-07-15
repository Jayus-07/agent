"""
api/routes/data.py — 数据接入 + 处理 + 资产 API

端点:
  POST /data/upload        — 上传 CSV/JSON 文件
  GET  /data/datasets       — 开源数据集列表
  POST /data/generate       — 模拟数据生成
  POST /pipeline/run        — 触发清洗任务
  GET  /pipeline/history    — 执行历史
  GET  /assets              — 数据资产列表
"""
import os, csv, json, io, uuid, time
from fastapi import APIRouter, UploadFile, File, Form
from backend.utils.logger import logger

router = APIRouter(prefix="/data", tags=["数据"])

UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)


@router.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    """上传 CSV/JSON 文件，返回字段识别和数据预览"""
    ext = file.filename.rsplit(".", 1)[-1].lower() if file.filename else ""
    if ext not in ("csv", "json"):
        return {"ok": False, "error": f"不支持的文件格式: .{ext}，请上传 CSV 或 JSON"}

    # 保存文件
    file_id = uuid.uuid4().hex[:8]
    save_path = os.path.join(UPLOAD_DIR, f"{file_id}_{file.filename}")
    content = await file.read()

    with open(save_path, "wb") as f:
        f.write(content)

    # 解析预览
    try:
        if ext == "csv":
            text = content.decode("utf-8")
            reader = csv.DictReader(io.StringIO(text))
            rows = list(reader)
        else:
            data = json.loads(content.decode("utf-8"))
            rows = [data] if isinstance(data, dict) else data

        fields = list(rows[0].keys()) if rows else []
        total_rows = len(rows)
        preview = rows[:5]

        logger.info(f"[Data Upload] {file.filename}: {total_rows} 行, {len(fields)} 字段")

        return {
            "ok": True,
            "file_id": file_id,
            "filename": file.filename,
            "size_bytes": len(content),
            "total_rows": total_rows,
            "fields": [
                {"name": f, "type": _infer_type([r.get(f) for r in preview])}
                for f in fields
            ],
            "preview": preview,
        }
    except Exception as e:
        os.remove(save_path)
        logger.error(f"[Data Upload] 解析失败: {e}")
        return {"ok": False, "error": f"文件解析失败: {str(e)}"}


def _infer_type(values: list) -> str:
    for v in values:
        if v is None or v == "":
            continue
        try:
            int(v)
            return "integer"
        except (ValueError, TypeError):
            pass
        try:
            float(v)
            return "float"
        except (ValueError, TypeError):
            pass
    return "string"


# ── 开源数据集列表 ──
_DATASETS = [
    {"id": "k1", "name": "Amazon Reviews 2023", "source": "Kaggle", "rows": 500000, "fields": ["review_id","product_id","rating","text","date"], "desc": "500K 条真实商品评论，含评分和文本"},
    {"id": "k2", "name": "Brazilian E-Commerce", "source": "Kaggle", "rows": 100000, "fields": ["order_id","customer_id","price","status","date"], "desc": "巴西电商订单数据，含物流和支付"},
    {"id": "k3", "name": "UK Retail Transaction", "source": "UCI", "rows": 500000, "fields": ["invoice_no","stock_code","quantity","price","date"], "desc": "英国零售交易记录"},
    {"id": "k4", "name": "Instacart Market Basket", "source": "Kaggle", "rows": 3000000, "fields": ["order_id","product_id","add_to_cart_order","reordered"], "desc": "300 万条购物篮数据"},
    # ── Data Collection Center 本地数据集 ──
    {"id": "dc-products", "name": "商品数据 (跨境电商)", "source": "Data Collection Center", "rows": 12, "fields": ["sku","名称","品类","品牌","售价","成本","平台","状态","上架日期"], "desc": "蓝牙耳机/保温杯/狗窝/婴儿监护器等 12 条商品，覆盖 5 大品类 4 个平台"},
    {"id": "dc-orders", "name": "订单数据 (跨境电商)", "source": "Data Collection Center", "rows": 15, "fields": ["订单号","SKU","数量","单价","金额","渠道","地区","状态","下单日期"], "desc": "Amazon/Shopify/eBay 订单，涵盖美英德日加 5 国，含已签收/已发货/已退货多状态"},
    {"id": "dc-shops", "name": "店铺数据 (跨境电商)", "source": "Data Collection Center", "rows": 8, "fields": ["店铺ID","名称","平台","地区","状态","商品数","评分"], "desc": "8 个线上店铺，分布 Amazon/Shopify/eBay，含已暂停店铺案例"},
    {"id": "dc-inventory", "name": "库存数据 (跨境电商)", "source": "Data Collection Center", "rows": 12, "fields": ["SKU","仓库","库存量","安全库存","预留量","最后补货","状态"], "desc": "多仓库库存快照，含偏低/断货预警"},
    {"id": "dc-suppliers", "name": "供应商数据 (跨境电商)", "source": "Data Collection Center", "rows": 10, "fields": ["供应商ID","名称","品类","品牌","地区","交期天数","不良率","评分"], "desc": "10 家供应商，覆盖中国/越南，含评估中供应商"},
]


@router.get("/datasets")
async def list_datasets():
    """开源数据集列表"""
    return {"datasets": _DATASETS, "total": len(_DATASETS)}


@router.post("/generate")
async def generate_data(types: list[str] = ["products"], count: int = 1000):
    """模拟数据生成

    优先从 PostgreSQL stg_* 表读取；数据库不可用时回退到 Data Collection Center
    本地数据集（StaticFetcher → JsonParser → CleanedData）
    """
    # ── 路径 1: 从 PostgreSQL 读取 ──
    try:
        import pandas as pd
        from backend.data_collection.config import DC_DATABASE_URL
        from sqlalchemy import create_engine

        engine = create_engine(DC_DATABASE_URL)
        result = {}
        for t in types:
            try:
                df = pd.read_sql(f"SELECT * FROM stg_{t} LIMIT {count}", engine)
                result[t] = {"rows": len(df), "columns": list(df.columns), "preview": df.head(5).to_dict(orient="records")}
            except Exception as e:
                result[t] = {"rows": 0, "columns": [], "error": str(e), "from": "db"}
        engine.dispose()

        has_data = any(v.get("rows", 0) > 0 for v in result.values())
        if has_data:
            return {"ok": True, "generated": result, "source": "postgresql", "task_id": uuid.uuid4().hex[:8]}
    except Exception:
        pass  # 数据库不可用 → 回退

    # ── 路径 2: 回退到 Data Collection Center 本地数据集 ──
    try:
        from backend.data_collection.fetchers.static_fetcher import StaticDataFetcher
        from backend.data_collection.parsers.json_parser import JsonParser
        from backend.data_collection.cleaners.default_cleaner import DefaultCleaner

        fetcher = StaticDataFetcher()
        parser = JsonParser()
        cleaner = DefaultCleaner()
        result = {}

        for t in types:
            try:
                raw = fetcher.fetch(f"static://datasets/{t}.json")
                parsed = parser.parse(raw)
                cleaned = cleaner.clean(parsed.records, source=f"static://datasets/{t}.json")

                records = cleaned.records[:min(count, len(cleaned.records))]
                result[t] = {
                    "rows": cleaned.row_count,
                    "columns": list(records[0].keys()) if records else [],
                    "preview": records[:5],
                    "from": "local_dataset",
                }
            except Exception as e:
                result[t] = {"rows": 0, "columns": [], "error": str(e), "from": "local_dataset"}

        logger.info(f"[Generate] 本地回退: types={types}, count={count} → {list(result.keys())}")
        return {"ok": True, "generated": result, "source": "local_dataset", "task_id": uuid.uuid4().hex[:8]}

    except Exception as e:
        logger.error(f"[Generate] 失败: {e}")
        return {"ok": False, "error": str(e)}


# ── 数据资产（复用 stg_* 表） ──

assets_router = APIRouter(prefix="/assets", tags=["数据资产"])


@assets_router.get("")
async def list_assets():
    """从 PostgreSQL stg_* 表查询数据资产"""
    try:
        import pandas as pd
        from backend.data_collection.config import DC_DATABASE_URL
        from sqlalchemy import create_engine

        engine = create_engine(DC_DATABASE_URL)
        tables = ["stg_products", "stg_orders", "stg_shops", "stg_inventory", "stg_suppliers"]
        assets = []
        for t in tables:
            try:
                df = pd.read_sql(f"SELECT count(*) as cnt FROM {t}", engine)
                rows = int(df.iloc[0, 0])
                cols_df = pd.read_sql(f"SELECT * FROM {t} LIMIT 0", engine)
                assets.append({
                    "id": t, "name": t, "source": "数据采集中心",
                    "rows": rows, "fields": len(cols_df.columns),
                    "field_names": list(cols_df.columns),
                    "quality": 100, "status": "就绪"
                })
            except Exception:
                pass
        engine.dispose()
        return {"assets": assets, "total": len(assets)}
    except Exception as e:
        return {"assets": [], "total": 0, "error": str(e)}


# ── 数据处理 Pipeline ──

pipeline_router = APIRouter(prefix="/pipeline", tags=["数据处理"])

_pipeline_jobs: dict = {}


@router.post("/pipeline/run")
async def run_pipeline(file_id: str = Form(...), steps: str = Form("detect,clean,dedup,convert")):
    """对已上传文件执行 Pandas 清洗 Pipeline"""
    import pandas as pd

    # 查找文件
    files = [f for f in os.listdir(UPLOAD_DIR) if f.startswith(file_id)]
    if not files:
        return {"ok": False, "error": "文件不存在"}

    filepath = os.path.join(UPLOAD_DIR, files[0])
    started = time.perf_counter()
    stages = []
    errors = 0

    try:
        df = pd.read_csv(filepath) if filepath.endswith(".csv") else pd.DataFrame(json.load(open(filepath)))
        input_rows = len(df)
        stages.append({"name": "字段检测", "status": "done", "rows": input_rows})

        if "clean" in steps:
            nulls_before = df.isna().sum().sum()
            for col in df.columns:
                if pd.api.types.is_numeric_dtype(df[col]):
                    df[col] = df[col].fillna(df[col].median())
                else:
                    df[col] = df[col].fillna("未知")
            stages.append({"name": "缺失值处理", "status": "done", "rows": len(df)})

        if "dedup" in steps:
            before = len(df)
            df = df.drop_duplicates()
            stages.append({"name": "去重", "status": "done", "rows": len(df), "removed": before - len(df)})

        if "convert" in steps:
            stages.append({"name": "格式转换", "status": "done", "rows": len(df)})

        output_rows = len(df)
        quality = round(output_rows / input_rows * 100, 1) if input_rows > 0 else 100
        elapsed_ms = (time.perf_counter() - started) * 1000

        job = {
            "id": file_id, "name": f"清洗: {files[0]}", "inputRows": input_rows,
            "outputRows": output_rows, "errors": errors, "quality": quality,
            "status": "done", "elapsed": f"{elapsed_ms/1000:.1f}s", "stages": stages,
        }
        _pipeline_jobs[file_id] = job

        logger.info(f"[Pipeline] {file_id}: {input_rows}→{output_rows} 行, quality={quality}%, {elapsed_ms:.0f}ms")
        return {"ok": True, "job": job}

    except Exception as e:
        logger.error(f"[Pipeline] 失败: {e}")
        return {"ok": False, "error": str(e)}


@router.get("/pipeline/history")
async def pipeline_history():
    """执行历史"""
    return {"jobs": list(_pipeline_jobs.values())[-20:]}


# ═══════════════════════════════════════════════════════════
# Data Collection Center — 采集任务 API
# ═══════════════════════════════════════════════════════════

_COLLECT_JOBS: dict = {}  # task_id → CollectResult

# 采集任务配置（与 demo_data_collection.py 保持一致）
_COLLECT_TASKS = {
    "products": {
        "source": "static://datasets/products.json", "table": "stg_products",
        "dedup_keys": ["sku"], "dataset_name": "products",
    },
    "orders": {
        "source": "static://datasets/orders.json", "table": "stg_orders",
        "dedup_keys": ["订单号"], "dataset_name": "orders",
    },
    "shops": {
        "source": "static://datasets/shops.json", "table": "stg_shops",
        "dedup_keys": ["店铺ID"], "dataset_name": "shops",
    },
    "inventory": {
        "source": "static://datasets/inventory.json", "table": "stg_inventory",
        "dedup_keys": ["SKU", "仓库"], "dataset_name": "inventory",
    },
    "suppliers": {
        "source": "static://datasets/suppliers.json", "table": "stg_suppliers",
        "dedup_keys": ["供应商ID"], "dataset_name": "suppliers",
    },
}


def _build_dcc_pipeline(enable_write: bool = False):
    """构建 Data Collection Center Pipeline 实例"""
    from backend.data_collection.fetchers.static_fetcher import StaticDataFetcher
    from backend.data_collection.parsers.json_parser import JsonParser
    from backend.data_collection.cleaners.default_cleaner import DefaultCleaner
    from backend.data_collection.analyzers.stats_analyzer import StatsAnalyzer

    writer = None
    if enable_write:
        try:
            from backend.data_collection.writers.sqlalchemy_writer import SQLAlchemyWriter
            from backend.data_collection.config import DC_DATABASE_URL
            writer = SQLAlchemyWriter(DC_DATABASE_URL)
        except Exception:
            pass

    from backend.data_collection.pipeline import CollectionPipeline
    return CollectionPipeline(
        fetcher=StaticDataFetcher(),
        parser=JsonParser(),
        cleaner=DefaultCleaner(),
        analyzer=StatsAnalyzer(),
        writer=writer,
    )


@router.post("/collect")
async def trigger_collect(
    dataset: str = Form("products"),
    enable_write: bool = Form(False),
):
    """触发单次数据采集任务（Data Collection Center Pipeline）

    dataset 可选值: products | orders | shops | inventory | suppliers
    返回完整的 CollectResult（含 parse/clean/analyze 各阶段统计）
    """
    task_config = _COLLECT_TASKS.get(dataset)
    if not task_config:
        return {
            "ok": False,
            "error": f"未知数据集: {dataset}，可选: {list(_COLLECT_TASKS.keys())}",
        }

    pipeline = _build_dcc_pipeline(enable_write=enable_write)

    try:
        result = pipeline.run(
            source=task_config["source"],
            table=task_config["table"],
            dedup_keys=task_config.get("dedup_keys"),
            analysis_config={
                "dataset_name": task_config.get("dataset_name", dataset),
                "groupby_keys": "auto",
            },
            write_mode="append",
        )

        task_id = result.task_id
        _COLLECT_JOBS[task_id] = result

        # 构建前端友好的响应
        response = {
            "ok": result.status == "success" or result.status == "partial",
            "task_id": task_id,
            "dataset": dataset,
            "status": result.status,
            "elapsed_ms": result.elapsed_ms,
        }

        if result.parsed:
            response["parsed_rows"] = result.parsed.record_count
        if result.cleaned:
            response["cleaned_rows"] = result.cleaned.row_count
            response["dedup_removed"] = result.cleaned.dedup_removed
            response["null_filled"] = result.cleaned.null_filled
        if result.analyzed and result.analyzed.summary:
            response["summary"] = {
                field: {"mean": round(stats["mean"], 2) if stats.get("mean") else None}
                for field, stats in result.analyzed.summary.items()
            }
        if result.write:
            response["inserted"] = result.write.inserted
        if result.error:
            response["error"] = result.error

        logger.info(
            f"[DCC Collect] {dataset}: status={result.status}, "
            f"parsed={response.get('parsed_rows',0)}, "
            f"cleaned={response.get('cleaned_rows',0)}, "
            f"{result.elapsed_ms:.0f}ms"
        )
        return response

    except Exception as e:
        logger.error(f"[DCC Collect] {dataset} 失败: {e}")
        return {"ok": False, "error": str(e)}


@router.post("/collect/all")
async def trigger_collect_all(enable_write: bool = Form(False)):
    """批量采集全部 5 个数据集（Data Collection Center Pipeline）

    按 products → orders → shops → inventory → suppliers 顺序执行。
    每个数据集返回独立的采集结果。
    """
    pipeline = _build_dcc_pipeline(enable_write=enable_write)
    results: list[dict] = []

    for dataset, task_config in _COLLECT_TASKS.items():
        try:
            result = pipeline.run(
                source=task_config["source"],
                table=task_config["table"],
                dedup_keys=task_config.get("dedup_keys"),
                analysis_config={
                    "dataset_name": task_config.get("dataset_name", dataset),
                    "groupby_keys": "auto",
                },
                write_mode="append",
            )
            _COLLECT_JOBS[result.task_id] = result

            item = {
                "dataset": dataset,
                "task_id": result.task_id,
                "status": result.status,
                "elapsed_ms": result.elapsed_ms,
                "parsed_rows": result.parsed.record_count if result.parsed else 0,
                "cleaned_rows": result.cleaned.row_count if result.cleaned else 0,
                "dedup_removed": result.cleaned.dedup_removed if result.cleaned else 0,
            }
            if result.write:
                item["inserted"] = result.write.inserted
            if result.error:
                item["error"] = result.error
            results.append(item)

        except Exception as e:
            logger.error(f"[DCC Collect All] {dataset} 失败: {e}")
            results.append({"dataset": dataset, "status": "failed", "error": str(e)})

    success_count = sum(1 for r in results if r["status"] in ("success", "partial"))
    total_ms = sum(r.get("elapsed_ms", 0) for r in results)
    total_parsed = sum(r.get("parsed_rows", 0) for r in results)
    total_cleaned = sum(r.get("cleaned_rows", 0) for r in results)

    logger.info(
        f"[DCC Collect All] {success_count}/{len(results)} 成功, "
        f"total_parsed={total_parsed}, total_cleaned={total_cleaned}, "
        f"{total_ms:.0f}ms"
    )

    return {
        "ok": True,
        "total": len(results),
        "success": success_count,
        "failed": len(results) - success_count,
        "total_parsed": total_parsed,
        "total_cleaned": total_cleaned,
        "total_elapsed_ms": round(total_ms, 1),
        "results": results,
    }


@router.get("/collect/history")
async def collect_history(limit: int = 20):
    """采集任务执行历史"""
    jobs = sorted(
        _COLLECT_JOBS.values(),
        key=lambda r: r.elapsed_ms if hasattr(r, "elapsed_ms") else 0,
        reverse=True,
    )[:limit]
    return {
        "total": len(_COLLECT_JOBS),
        "jobs": [
            {
                "task_id": r.task_id,
                "source": r.source,
                "status": r.status,
                "elapsed_ms": r.elapsed_ms,
                "parsed_rows": r.parsed.record_count if r.parsed else 0,
                "cleaned_rows": r.cleaned.row_count if r.cleaned else 0,
            }
            for r in jobs
        ],
    }
