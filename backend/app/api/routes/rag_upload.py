"""RAG 上传路由 — PR-2.x 从 rag.py 抽出。"""
import asyncio, json, os, shutil, threading, time, uuid
from asyncio import Queue
from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Request, Depends
from fastapi.responses import StreamingResponse
from backend.app.api.deps import (
    get_rag_pipeline,
    require_rag_ready,
    require_rag_user,
    require_rag_editor,
)
from backend.app.api.identity import require_principal, resolve_identity, resolve_principal
from backend.config.rag import (
    RAG_MAX_FILE_SIZE, RAG_TMP_DIR, RAG_UPLOAD_CHUNK_SIZE,
    RAG_UPLOAD_EMIT_BYTES, RAG_UPLOAD_EMIT_MS, RAG_UPLOAD_PATH_GUARD,
)
from backend.config.rag import (
    RAG_MAX_CONCURRENT_INDEX,
    RAG_SSE_REDIS_POLL_MAX_SECONDS,
    RAG_SSE_REDIS_POLL_SECONDS,
)
# F7: IncrementalIndexer 不在模块顶层导入（导入链含 langchain/tracer 等重依赖），
# 改为 _do_index_sync 内惰性导入，路由模块冷启动不再被拖慢。
from backend.rag.progress_listener import ProgressListener
# 显式导入替代 import *（详见 rag_documents.py 同处注释）
from backend.app.api.routes._rag_shared import (
    _extract_source,
    _get_registry,
    _progress_queues,
    _progress_owners,
    _safe_log_op,
    _sse_encode,
    sanitize_doc_row,
)
from backend.rag.authz import RagAuthorization, RagAuthorizationError
from backend.rag.indexing.publish import (
    SupersededCandidate,
    cleanup_candidate_by_generation,
)
from backend.shared.logger import logger


def _cleanup_candidate_artifacts(upload_id: str, staging_path: str = "") -> None:
    """按运行记录清理候选代次产物（候选 collection 行 + BM25 staging 目录）。

    只在失败终态调用；全部动作幂等且不触碰主 collection / 正式 BM25。
    """
    try:
        from backend.config.database import (
            BM25_INDEX_DIR,
            CHROMA_PATH,
            DOC_DB_PATH,
        )
        from backend.rag.indexing.index_run_store_pg import get_index_run_store

        run = get_index_run_store().get_run(upload_id)
        generation = (run or {}).get("generation", "")
        if not generation:
            return
        cleanup_candidate_by_generation(
            generation,
            chroma_path=str(CHROMA_PATH),
            doc_db_path=str(DOC_DB_PATH),
            bm25_index_dir=str(BM25_INDEX_DIR),
        )
    except Exception as e:  # noqa: BLE001 — 清理失败留痕，残留交由 Sweeper
        logger.warning(f"[RAG] 候选产物清理失败 (upload={upload_id}): {e}")

router = APIRouter(dependencies=[Depends(require_rag_user)])


# ============ 文件锁（P1-2 防止同文件并发 race condition）============
# 背景:_do_index_sync 在线程池跑,duplicate 检测和 reindex 流程不是原子的。
# 两个并发请求同时上传同一文件可能都通过 duplicate 检测,然后都走到 _index_file,
# 导致同 doc_id 被写两次到向量库（rerank 阶段被同 chunk 命中两次,稀释 mrr）。
#
# 修法:Windows 用 msvcrt.locking,Linux/macOS 用 fcntl.flock。
# 用非阻塞锁 — 第二个请求立即抛 FileLockedByOtherError,而不是阻塞等待。
# 阻塞等待会让前端 SSE 超时,而且浪费资源。
class FileLockedByOtherError(Exception):
    """同文件正在被另一个请求处理,当前请求拒绝（避免双写向量库）。"""


class UploadIdempotencyConflict(Exception):
    """同幂等键不同内容的上传（或与在途任务冲突）——HTTP 层映射 409。"""

    def __init__(self, message: str, *, upload_id: str = ""):
        super().__init__(message)
        self.upload_id = upload_id


# Stale 锁 TTL:进程崩溃残留的 .lock 超过该时长后允许被新请求接管(自愈)。
# 背景:旧实现无任何回收路径,索引中途崩溃后 .lock 永久残留,
# 同名文件此后每次上传都被 FileLockedByOtherError 拒绝。
# 取 30 分钟:语义是"心跳超时判定"——持锁方有心跳线程持续刷新时间戳,
# 活锁永不超时(超长索引任务不再被误抢);持有进程死亡后心跳停止,
# 超过该时长即可安全接管。取值需远大于心跳间隔(LOCK_HEARTBEAT_SECONDS)。
LOCK_STALE_SECONDS = 30 * 60

# 锁心跳间隔:持锁期间每 N 秒重写 .lock 时间戳。判死需 TTL >> 该值。
LOCK_HEARTBEAT_SECONDS = 60.0


def _lock_age_seconds(lock_path: str) -> float | None:
    """读锁的已存在秒数:优先解析 acquire 时写入的时间戳(payload 第 2 行),
    不可解析时回退文件 mtime;都拿不到返回 None(保守不接管)。"""
    try:
        with open(lock_path, "r", encoding="utf-8", errors="replace") as f:
            f.readline()  # 第 1 行是 pid
            ts_line = f.readline().strip()
        ts = float(ts_line)
        if ts > 0:
            return time.time() - ts
    except (OSError, ValueError):
        pass
    try:
        return time.time() - os.path.getmtime(lock_path)
    except OSError:
        return None


def _try_create_lock(lock_path: str) -> int | None:
    """原子创建锁文件:成功返回 fd,已被占用返回 None。"""
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
    except FileExistsError:
        return None
    try:
        os.write(fd, f"{os.getpid()}\n{time.time()}\n".encode())
    except OSError:
        pass  # 写入失败不影响锁本身
    return fd


def acquire_index_lock(filepath: str) -> int:
    """对 filepath 加非阻塞排他锁(用 .lockfile 原子创建方案)。

    Args:
        filepath: 文件绝对路径。

    Returns:
        文件描述符 fd(指向 .lock 文件)。调用方负责 release_index_lock(fd, filepath)。
        实际为兼容性返回 fd,但调用方必须同时传 filepath 给 release。

    Raises:
        FileLockedByOtherError: 文件已被另一个请求加锁(且锁未超 TTL)。

    设计:
      - 用 sidecar `.lock` 文件 + O_CREAT | O_EXCL 实现原子互斥
      - 跨平台、纯标准库,无需 pywin32/fcntl
      - 跨进程安全(O_EXCL 是 atomic on most filesystems)
      - 跨线程安全(同一进程内 fd 唯一)
      - Stale 自愈:锁存在但超 LOCK_STALE_SECONDS → unlink 后重试一次;
        两个请求同时抢 stale 锁时,后到者 O_EXCL 失败 → 保守报锁占用
    """
    lock_path = filepath + ".lock"
    # B 阶段：正式目录在上传阶段不再预创建（正式文件发布时才落位），
    # 锁文件要与正式文件同目录 → 这里负责确保父目录存在
    parent = os.path.dirname(lock_path)
    if parent:
        try:
            os.makedirs(parent, exist_ok=True)
        except OSError:
            pass
    fd = _try_create_lock(lock_path)
    if fd is not None:
        return fd

    # 被占用 → 检查是否崩溃残留的 stale 锁(时间戳/mtime 超 TTL)
    age = _lock_age_seconds(lock_path)
    if age is not None and age > LOCK_STALE_SECONDS:
        try:
            os.unlink(lock_path)
        except OSError:
            pass
        logger.warning(f"[RAG] 接管 stale 锁 (age={int(age)}s): {lock_path}")
        fd = _try_create_lock(lock_path)
        if fd is not None:
            return fd

    holder_pid = None
    try:
        with open(lock_path, "r", encoding="utf-8", errors="replace") as f:
            holder_pid = f.readline().strip() or None
    except OSError:
        pass
    # 细节（服务器绝对路径/持锁 pid）只进服务端日志：异常信息会原样展示在
    # 前端与操作审计 detail 里，不应泄漏服务器文件系统结构与进程号
    logger.warning(f"[RAG] 锁竞争拒绝: {filepath} (holder_pid={holder_pid})")
    raise FileLockedByOtherError(
        f"文件 {os.path.basename(filepath)} 正在被另一个上传请求处理，请稍后重试"
    )


def _pdf_has_text_layer(path: str, max_pages: int = 10) -> bool | None:
    """检测 PDF 是否有文本层（扫描件预检，纯函数便于测试）。

    Returns:
        True  — 检查的前 max_pages 页中存在足够文本 → 正常解析
        False — 检查页全部无文本 → 扫描件/纯图片，索引必然失败，入口即拒
        None  — 无法判定（加密/损坏/PyMuPDF 异常）→ 放行，交给解析层报错

    判定阈值：单页可提取文本 ≥ 20 字符即认为有文本层（页码/水印级噪音
    不算）。只查前 max_pages 页：扫描件通常整本无文本，无需全量扫描。
    """
    try:
        import fitz  # PyMuPDF（pdf_parser 同源依赖）
        with fitz.open(path) as doc:
            if doc.is_encrypted:
                return None
            for i, page in enumerate(doc):
                if i >= max_pages:
                    break
                if len(page.get_text().strip()) >= 20:
                    return True
            return False
    except Exception:
        return None


def sha256_of_file(path: str, chunk_size: int = 8 * 1024 * 1024) -> str:
    """分块流式计算文件 SHA256 — 避免整文件读入内存。

    背景:旧实现 hashlib.sha256(f.read()) 对 50MB 上限的文件一次性
    读入内存,并发上传时内存峰值翻倍。改为固定块大小流式读取。
    """
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk_size)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def release_index_lock(fd: int, filepath: str = "") -> None:
    """释放 acquire_index_lock 获取的锁 + 关闭 fd。

    设计:fd 指向 .lock 文件,关闭 fd 后 unlink .lock 文件(用 filepath 推导)。
    安全:
      - 重复 release / fd=-1 / filepath 空 都不抛异常
      - unlink 失败 swallow(可能在另一进程已被删)
    """
    if fd < 0:
        return
    try:
        os.close(fd)
    except OSError:
        pass
    if filepath:
        lock_path = filepath + ".lock"
        try:
            os.unlink(lock_path)
        except OSError:
            pass


def _start_lock_heartbeat(lock_path: str, stop_event: threading.Event) -> threading.Thread:
    """启动锁心跳线程：持锁期间定期重写 .lock 时间戳。

    把 stale 判定从"固定 30 分钟"变为"心跳超时"：超长索引任务（真实存在，
    30 分钟 TTL 就是按它定的上限）不再被并发请求误判接管；持有进程崩溃后
    心跳停止，时间戳停滞，TTL 后照常自愈接管——两种场景都正确。

    防御：每拍心跳先校验锁文件仍属于本进程（pid 匹配），锁已被他人接管或
    清理时立即退出，绝不复活已易主的锁文件。
    """
    my_pid = str(os.getpid())

    def _beat():
        while not stop_event.wait(LOCK_HEARTBEAT_SECONDS):
            try:
                with open(lock_path, "r", encoding="utf-8", errors="replace") as f:
                    if f.readline().strip() != my_pid:
                        return  # 锁已易主/被清理，退出
                with open(lock_path, "w", encoding="utf-8") as f:
                    f.write(f"{my_pid}\n{time.time()}\n")
            except OSError:
                return  # 锁文件已消失（正常释放），心跳退出

    t = threading.Thread(target=_beat, name="lock-heartbeat", daemon=True)
    t.start()
    return t


def cleanup_stale_upload_artifacts(docs_dir: str, tmp_dir: str,
                                   max_age_seconds: float = LOCK_STALE_SECONDS) -> dict:
    """启动清理:回收 docs 目录下超龄 .lock 与中断上传遗留的孤儿 tmp 文件。

    两类残留都来自进程崩溃/强杀,不清理会持续积累:
      - .lock 残留 → 对应文件永久被 FileLockedByOtherError 拒绝
      - tmp 残留 → 磁盘占用泄漏
    只删 mtime 超过 max_age_seconds 的文件,不误伤进行中的上传/索引。

    Returns: {"locks_removed": int, "tmp_removed": int}
    """
    counts = {"locks_removed": 0, "tmp_removed": 0}
    now = time.time()

    for root, _dirs, files in os.walk(docs_dir):
        for fname in files:
            if not fname.endswith(".lock"):
                continue
            p = os.path.join(root, fname)
            try:
                if now - os.path.getmtime(p) > max_age_seconds:
                    os.unlink(p)
                    counts["locks_removed"] += 1
            except OSError as e:
                logger.warning(f"[RAG] stale 锁清理失败 {p}: {e}")

    if os.path.isdir(tmp_dir):
        # 非终态运行持有的暂存文件豁免（恢复 sweeper 可能随时重投，
        # 删了会让重跑任务断源）；其余孤儿按超龄清理
        protected: set[str] = set()
        try:
            from backend.rag.indexing.index_run_store_pg import get_index_run_store
            protected = get_index_run_store().list_non_terminal_staging_paths()
        except Exception as e:
            logger.warning(f"[RAG] 暂存豁免名单读取失败（按无豁免处理）: {e}")
        for root, _dirs, files in os.walk(tmp_dir):
            for fname in files:
                p = os.path.join(root, fname)
                if p in protected:
                    continue
                try:
                    if now - os.path.getmtime(p) > max_age_seconds:
                        os.unlink(p)
                        counts["tmp_removed"] += 1
                except OSError as e:
                    logger.warning(f"[RAG] 孤儿 tmp 清理失败 {p}: {e}")

    if counts["locks_removed"] or counts["tmp_removed"]:
        logger.info(f"[RAG] 启动清理: {counts['locks_removed']} 个 stale 锁, "
                    f"{counts['tmp_removed']} 个孤儿 tmp")
    return counts


# ============ F11: 进度队列过期清理 ============
# 背景:旧实现的过期清理仅在新上传时触发（sync_upload_impl 内联），
# 无上传流量时过期队列永久滞留。现抽为独立函数，除上传入口复用外，
# 由 server 启动的后台定时任务周期性驱动。
def cleanup_expired_progress_queues() -> int:
    """清理超过 PROGRESS_QUEUE_TTL_SECONDS 的进度队列，返回清理条数。

    安全:队列残留只发生在客户端从未订阅 SSE 且后台任务已结束的极端场景；
    超 30 分钟的队列不可能仍有活跃消费者（后台索引远超此时长）。
    """
    now = time.time()
    expired = [uid for uid, q in _progress_queues.items()
               if getattr(q, "_created_at", 0) < now - PROGRESS_QUEUE_TTL_SECONDS]
    for uid in expired:
        _progress_queues.pop(uid, None)
        _progress_owners.pop(uid, None)
        _celery_routed.discard(uid)
    if expired:
        logger.info(f"[RAG] 清理过期进度队列 {len(expired)} 个")
    return len(expired)


async def progress_queue_gc_loop(interval_seconds: float = 300) -> None:
    """后台定时清理过期进度队列（F11），由 server 启动时 create_task。

    异常不退出循环 — GC 任务自身挂掉比队列滞留更糟，单轮失败只告警。
    """
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            cleanup_expired_progress_queues()
        except Exception as e:
            logger.warning(f"[RAG] 进度队列定时清理异常: {e}")


# ============ MIME 白名单（P0-2 收紧 / F6 单一来源）============
# 设计要点：
#   1. 支持的扩展名从解析器注册表 PARSABLE_EXTS 派生（F6 单一来源），
#      与解析层永远一致；新增解析器自动对上传开放，无需同步多处名单。
#   2. content_type 为空或显式 application/octet-stream 时由 _validate_mime
#      放行，落盘后靠魔数校验兜底（Windows/浏览器客户端常对 docx 等声明
#      octet-stream，MIME 层拒绝会误伤合法文件）；白名单表只登记
#      "显式声明时允许的具体 MIME"。
#   3. 文本格式的魔数探测：NUL 字节 + UTF-16 BOM 拒绝二进制/双字节伪装。
#   4. 模块级常量，方便纯函数 import 测试。
from backend.rag.preprocessing.parser import PARSABLE_EXTS

# 后台索引任务的存活引用集：防止 fire-and-forget task 被 GC / 异常静默丢失
_background_index_tasks: set[asyncio.Task] = set()

# 索引并发闸门（懒创建信号量，见 _get_index_semaphore）；env 读取收口 config/rag.py
_INDEX_CONCURRENCY_LIMIT = RAG_MAX_CONCURRENT_INDEX
_index_semaphore: asyncio.Semaphore | None = None

_MIME_BY_EXT: dict[str, set[str]] = {
    "pdf":      {"application/pdf"},
    "md":       {"text/markdown", "text/plain"},
    "markdown": {"text/markdown", "text/plain"},
    "txt":      {"text/plain"},
    "docx":     {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
    "xlsx":     {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
    # csv 解析器注册后同步补齐（F6 单一来源派生，避免漂移）；
    # 浏览器/客户端常以 text/plain 声明，一并放行。
    "csv":      {"text/csv", "text/plain"},
}

ALLOWED_MIME_TYPES: dict[str, set[str]] = {
    ext: mimes for ext, mimes in _MIME_BY_EXT.items()
    if f".{ext}" in PARSABLE_EXTS
}

SUPPORTED_EXTS: frozenset[str] = frozenset(ALLOWED_MIME_TYPES.keys())

# F10: Content-Length 预检余量 — multipart 封装（boundary + 头字段）比裸文件大
# 几百字节到几 KB，贴上限的文件会被误拒；预检放宽一个余量，
# 精确上限仍由 sync_upload_impl 流式字节计数强制执行（双保险不放松）。
_MULTIPART_OVERHEAD = 16 * 1024

# F11: 进度队列过期阈值（与 sync_upload_impl 内联清理同口径）
PROGRESS_QUEUE_TTL_SECONDS = 1800

# P2: SSE 心跳间隔（秒）— 无事件期间发 ": keepalive" 注释帧防代理断连。
# 抽为常量便于测试 patch（生产值 15s，测试中可缩到毫秒级）。
SSE_KEEPALIVE_TIMEOUT_SECONDS = 15.0


def _validate_mime(ext: str, content_type: str | None) -> tuple[bool, str]:
    """校验 MIME 与扩展名一致性（纯函数）。

    Args:
        ext: 文件扩展名（小写，无点），如 "md" / "pdf"。
        content_type: HTTP 声明的 Content-Type（含 charset 后缀也 OK），可能为 None/""。

    Returns:
        (ok, error_msg) — ok=True 时 error_msg 为空字符串。

    规则:
      - ext 不在 SUPPORTED_EXTS → 拒（unsupported ext）
      - content_type 为 None / "" → 通过（走 magic 校验兜底）
      - 显式 content_type（去 charset 后）必须在 ALLOWED_MIME_TYPES[ext] 内
      - application/octet-stream 放行：这是 Windows / 部分浏览器客户端对
        "未知二进制"的通用声明，一律拒绝会误伤合法的 docx/xlsx 上传；
        文件真实性由落盘后的魔数校验兜底（PDF %PDF- / OOXML PK\x03\x04 /
        文本格式 NUL + UTF-16 BOM 探测）。
    """
    if ext not in SUPPORTED_EXTS:
        return False, f"unsupported ext: .{ext}"
    if not content_type or not content_type.strip():
        # content_type 为空（curl 命令行等场景）→ 走 magic 校验兜底
        return True, ""
    # strip charset 参数（如 "text/plain; charset=utf-8" → "text/plain"）
    ctype = content_type.split(";", 1)[0].strip().lower()
    if not ctype:
        return True, ""
    # octet-stream 放行（历史教训改为由魔数校验兜底）：旧实现在此直接拒绝，
    # 结果是浏览器/Windows 客户端上传 docx（常声明 octet-stream）被误拒。
    # 二进制格式有强魔数（%PDF- / PK\x03\x04），文本格式有 NUL + BOM 探测，
    # 伪装文件进不到索引环节。
    if ctype == "application/octet-stream":
        return True, ""
    if ctype not in ALLOWED_MIME_TYPES[ext]:
        return False, f"MIME type not allowed for .{ext}: {ctype}"
    return True, ""


@router.post("/upload", dependencies=[Depends(require_rag_editor)])
async def upload_document(request: Request, file: UploadFile = File(...),
                          kb_id: str = Form("policy_general"),
                          department: str = Form("general")):
    """P0-1 流式上传: 临时文件 + atomic rename + 双保险大小限制 + SSE 进度"""
    require_rag_ready()
    # 归属裁决（2026-10-01 权限收口）：department 只认主体自身部门（admin 可
    # 代传任意合法部门），KB 必须 ∈ 主体可见集；请求体自报部门不再单独放行。
    principal = resolve_principal(request)
    try:
        authz = RagAuthorization.build(principal)
    except RagAuthorizationError as e:
        logger.warning(f"[RAG] 上传授权失败: {e}")
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=403, content={"ok": False, "error": "授权服务暂不可用，已拒绝上传"})
    up_ok, up_reason = authz.can_upload_to(kb_id, department)
    if not up_ok:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=403, content={"ok": False, "error": up_reason})
    identity = resolve_identity(request)
    from backend.config.rag import (
        RAG_MAX_FILE_SIZE, RAG_TMP_DIR, RAG_UPLOAD_CHUNK_SIZE,
        RAG_UPLOAD_EMIT_BYTES, RAG_UPLOAD_EMIT_MS,
    )

    ext = (file.filename or "").rsplit(".", 1)[-1].lower()
    ok, err = _validate_mime(ext, file.content_type)
    if not ok:
        return {"ok": False, "error": err}

    max_size = RAG_MAX_FILE_SIZE * 1024 * 1024
    cl = request.headers.get("content-length")
    # F10: 预检带 multipart 余量，贴上限文件不误拒；超限仍由流式计数拦截
    if cl and cl.isdigit() and int(cl) > max_size + _MULTIPART_OVERHEAD:
        return {"ok": False, "error": f"file too large (max {RAG_MAX_FILE_SIZE}MB)"}

    try:
        # sync_upload_impl 现在是 async def, 不需要 threadpool 包装
        # (file.read() 是 async, 必须在 async context 调, 同步 I/O 也用 sync open/write)
        result = await sync_upload_impl(
            file, request, max_size,
            RAG_TMP_DIR, RAG_UPLOAD_CHUNK_SIZE, RAG_UPLOAD_EMIT_BYTES, RAG_UPLOAD_EMIT_MS,
            kb_id=kb_id, department=department,
            tenant_id=identity.tenant_id,
            actor_id=identity.user_id,
            idempotency_key=(request.headers.get("Idempotency-Key") or "").strip(),
        )
    except Exception as e:
        return {"ok": False, "error": f"upload failed: {type(e).__name__}: {e}"}
    if not result.get("ok"):
        if result.get("status_code") == 422:
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=422, content=result)
        return result

    # ── 可信请求在请求路径内派发（B 阶段）：幂等冲突/提交失败必须在 HTTP
    # 响应前暴露（409/错误响应），不能等 background task 静默吞掉。
    task_kwargs = {
        "upload_id": result["upload_id"], "filepath": result["filepath"],
        "staging_path": result.get("staging_path", ""),
        "generation": result.get("generation", ""),
        "file_hash": result.get("file_hash", ""),
        "filename": result["filename"], "kb_id": kb_id,
        "department": department, "source": result["source"],
        "batch_id": result["batch_id"],
        "upload_elapsed_ms": result.get("upload_elapsed_ms"),
        # Phase1 Step8：仅作 tasks 行归属（_dispatch_index_to_celery
        # 内 pop 掉，不进 Celery 消息）
        "actor_id": identity.user_id, "tenant_id": identity.tenant_id,
        "was_overwrite": result.get("was_overwrite", False),
    }
    pre_dispatched = None
    if identity.tenant_id and identity.user_id:
        try:
            pre_dispatched = await asyncio.to_thread(
                _dispatch_index_with_idempotency,
                task_kwargs=task_kwargs,
                file_hash=result.get("file_hash", ""),
                tenant_id=identity.tenant_id,
                actor_id=identity.user_id,
                idempotency_key=result.get("idempotency_key", ""),
            )
        except UploadIdempotencyConflict as conflict:
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=409,
                content={"ok": False, "conflict": True,
                         "error": str(conflict),
                         "upload_id": conflict.upload_id},
            )
        except Exception as dispatch_err:
            # 可信请求不能在幂等 Redis/PG 或任务提交失败时静默降级到
            # 进程内执行（会绕过「只执行一次」保证）——显式失败，
            # 暂存文件保留供重试（同键同内容重试会复用幂等语义）。
            logger.error(f"[RAG] 索引任务提交失败: {dispatch_err}")
            return {"ok": False,
                    "error": f"索引任务提交失败: {type(dispatch_err).__name__}: {dispatch_err}"}

    # 附着语义：同内容更早的任务在途 → 客户端应订阅【那条】任务的 SSE
    attached_id = ""
    if isinstance(pre_dispatched, dict) and pre_dispatched.get("attached"):
        attached_id = str(pre_dispatched.get("upload_id") or "")
        if str(pre_dispatched.get("run_status")) == "published":
            # 附着到「已发布」任务：内容完全一致 = duplicate 语义。进度镜像
            # 可能已过期（TTL），这里重发一次终态，保证客户端 SSE 拿得到终态
            # （与 Worker 重复投递时 hash-dedup 发 duplicate 同一口径）。
            _write_progress_redis(
                attached_id, "duplicate", "文件已存在，未重复索引",
                owner=(identity.tenant_id, identity.user_id),
                doc=sanitize_doc_row(_get_registry().get_by_path(
                    result["filepath"]) or {}))
            # 本进程重发终态 → 订阅必须走 Redis 轮询通道（本进程没有该任务
            # 的进程内队列）；打路由标记让 SSE 无缝切到 Redis 通道
            _celery_routed.add(attached_id)
            _progress_owners[attached_id] = (identity.tenant_id, identity.user_id)
            return {"ok": True, "duplicate_of": attached_id,
                    "upload_id": attached_id, "filename": result["filename"]}

    if not attached_id:
        task = asyncio.create_task(_run_index_background(
            result["upload_id"], result["filepath"], result["filename"],
            result["source"], result["batch_id"], kb_id=kb_id, department=department,
            upload_elapsed_ms=result.get("upload_elapsed_ms"),
            was_overwrite=result.get("was_overwrite", False),
            file_hash=result.get("file_hash", ""),
            staging_path=result.get("staging_path", ""),
            generation=result.get("generation", ""),
            tenant_id=result.get("tenant_id", ""),
            actor_id=result.get("actor_id", ""),
            idempotency_key=result.get("idempotency_key", ""),
            pre_dispatched=pre_dispatched,
            task_kwargs=None if pre_dispatched is not None else task_kwargs,
        ))
        # 持有引用：无引用的 fire-and-forget task 可能被 GC，异常也会被静默吞掉
        _background_index_tasks.add(task)
        task.add_done_callback(_background_index_tasks.discard)
    return {"ok": True, "duplicate_of": attached_id or None,
            "upload_id": attached_id or result["upload_id"],
            "filename": result["filename"]}




async def sync_upload_impl(
    file, request, max_size, tmp_dir, chunk_size, emit_bytes, emit_ms,
    kb_id: str = "policy_general", department: str = "general",
    tenant_id: str = "", actor_id: str = "", idempotency_key: str = "",
) -> dict:
    """P0-1 流式上传 (async def, 直接在 upload_document 事件循环里跑).
    file.read() 是 async method, 必须 await. 写文件是 sync (open + write).
    """
    from backend.config.database import DOCS_DIRECTORY as _DOCS_DIRECTORY

    # _progress_queues / _extract_source 现为模块级显式导入，直接引用即可
    # （原先经 globals() 取值是为了绕开 import * 的名字丢失问题）

    raw_filename = str(file.filename or "")
    if RAG_UPLOAD_PATH_GUARD:
        from backend.rag.indexing.upload_path_guard import test_artifact_path_reason

        reason = test_artifact_path_reason(raw_filename)
        if reason:
            from backend.security.events import record_security_event

            record_security_event(
                "INPUT_GUARD_BLOCK", category="TEST_ARTIFACT_REJECTED",
                detail={"path_reason": reason},
            )
            return {"ok": False, "status_code": 422,
                    "error": "测试临时路径文件被拒绝"}
    safe_name = os.path.basename(raw_filename)
    # 修复中文文件名乱码：尝试多种编码回编解码
    if safe_name:
        for enc in ('latin-1', 'cp1252', 'iso-8859-1'):
            try:
                raw = safe_name.encode(enc)
                candidate = raw.decode('utf-8')
                # 成功解码且包含中文字符 → 采纳
                if any('一' <= c <= '鿿' for c in candidate):
                    safe_name = candidate
                    break
            except (UnicodeDecodeError, UnicodeEncodeError):
                continue
    if not safe_name or safe_name.startswith("."):
        return {"ok": False, "error": "invalid filename"}
    # P1 fix: 用 realpath 解析 backend/data junction 符号链接
    # 避免同一物理文件被存为两条不同 file_path 记录（SQLite 主键冲突 → 重复 doc_id）
    abs_docs_dir = os.path.realpath(_DOCS_DIRECTORY)
    final_dir = os.path.abspath(os.path.join(abs_docs_dir, kb_id, department))
    final_path = os.path.normpath(os.path.join(final_dir, safe_name))
    # 末尾再 realpath 一次（防御 abspath 残留符号链接组件）
    final_path = os.path.realpath(final_path)
    try:
        if os.path.commonpath([abs_docs_dir, final_path]) != abs_docs_dir:
            return {"ok": False, "error": "invalid path"}
    except ValueError:
        return {"ok": False, "error": "invalid path"}

    # F9: was_overwrite 检测移到 os.replace 前一刻（原在函数入口，与 replace
    # 隔了整段上传+校验流程，TOCTOU 窗口内并发同名上传可基于陈旧状态决策
    # cleanup 策略）。贴近 replace 后窗口缩到备份+replace 两条语句。

    ext = safe_name.rsplit(".", 1)[-1].lower()
    # upload_id 语义（B 阶段收口）：任务标识，不再是可复用的写入路径。
    #   - 显式幂等键 → 确定性 id（同键同内容重试 = 同一任务与进度）；
    #   - 无键请求一律随机新任务（同名不同内容互不混写）。
    if tenant_id and actor_id and idempotency_key:
        import hashlib
        upload_id = hashlib.sha256(
            f"{tenant_id}:{actor_id}:{idempotency_key}".encode("utf-8")
        ).hexdigest()[:12]
    else:
        upload_id = uuid.uuid4().hex[:12]
    # generation：本次候选代次（向量候选 collection / BM25 staging 目录共用）
    generation = uuid.uuid4().hex[:16]
    # 暂存文件：独立随机命名、不可变——Worker 只读它，索引成功才由发布
    # 协议 os.replace 到正式路径（旧实现在上传 API 进程、拿锁之前就覆盖
    # 正式文件，是并发覆盖竞态的根源，已废除）。
    staging_dir = os.path.join(tmp_dir, "staging")
    os.makedirs(staging_dir, exist_ok=True)
    staging_path = os.path.abspath(os.path.join(staging_dir, f"{upload_id}.{generation}.{ext}"))

    # 真实 HTTP 上传耗时（POST body 接收 + 写暂存文件）
    _upload_t0_sync = time.time()

    # F11: 过期队列清理抽为独立函数（定时 GC 复用同一逻辑）
    cleanup_expired_progress_queues()

    queue: Queue = Queue()
    queue._created_at = time.time()
    _progress_queues[upload_id] = queue
    # SSE 订阅归属绑定（2026-10-01 权限收口）：只有上传者本人/admin 可订阅
    _progress_owners[upload_id] = (tenant_id, actor_id)

    # 进度推送: 在 async context 直接 queue.put_nowait (因为是 asyncio.Queue, 跨 coroutine 同一 loop OK)
    def _safe_put(evt):
        queue.put_nowait(evt)

    try:
        import hashlib as _hashlib
        total = 0
        last_emit_bytes = 0
        last_emit_time = time.time()
        cl_str = request.headers.get("content-length", "0")
        cl_int = int(cl_str) if cl_str.isdigit() else None
        hasher = _hashlib.sha256()

        with open(staging_path, "wb") as f:
            while True:
                chunk = await file.read(chunk_size)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_size:
                    # F1 修复:超限拒绝必须清理临时文件。
                    f.close()
                    try:
                        os.unlink(staging_path)
                    except OSError as cleanup_error:
                        logger.warning(f"[RAG] 超限上传暂存文件清理失败: {cleanup_error}")
                    return {"ok": False, "error": f"file too large (max {max_size//1024//1024}MB, uploaded {total} bytes)"}
                f.write(chunk)
                # 流式计算 SHA256（B 阶段）：哈希与字节同源，Worker 校验
                # 「上传源哈希 = 处理哈希」不再依赖二次读盘
                hasher.update(chunk)

                now_emit = time.time()
                if total - last_emit_bytes >= emit_bytes or (now_emit - last_emit_time) * 1000 >= emit_ms:
                    progress = int(total * 100 / max(cl_int or total, 1))
                    _safe_put({
                        "stage": "uploading",
                        "progress": min(progress, 99),
                        "bytes": total,
                    })
                    last_emit_bytes = total
                    last_emit_time = now_emit

        file_hash = hasher.hexdigest()
        # P1-8: 入口校验 — 空文件与损坏文件（魔数）直接拒绝，避免后台索引阶段才失败
        if total == 0:
            try:
                os.unlink(staging_path)
            except OSError:
                pass
            return {"ok": False, "error": "file is empty"}
        with open(staging_path, "rb") as _f:
            head = _f.read(8192)
        _magic_ok = True
        if ext == "pdf" and not head.startswith(b"%PDF-"):
            _magic_ok = False
        elif ext in ("docx", "xlsx") and not head.startswith(b"PK\x03\x04"):
            # F6: xlsx 同为 OOXML zip 容器，魔数与 docx 一致
            _magic_ok = False
        elif ext in ("md", "markdown", "txt", "csv") and (
            b"\x00" in head or head[:2] in (b"\xff\xfe", b"\xfe\xff")
        ):
            # P2: 文本格式无魔数可查,用 NUL 字节探测拒绝二进制伪装;
            # UTF-16 BOM 同拒（解析器只有 utf-8/gbk 链路,UTF-16 会整体乱码）。
            # 历史教训:P0-1 二进制/乱码文件曾直接进入 doc_db。
            _magic_ok = False
        if not _magic_ok:
            try:
                os.unlink(staging_path)
            except OSError:
                pass
            return {"ok": False, "error": f"file is corrupted or not a valid .{ext} file (magic check failed)"}
        # 扫描件预检：PDF 无文本层时，OCR 兜底已启用（RAG_OCR_PROVIDER != off）
        # 则放行交给索引链路走 OCR；OCR 关闭时索引必然产出 0 chunk，
        # 入口直接明确拒绝，不让用户等后台索引几十秒才报错（预检失败不误杀）
        if ext == "pdf":
            from backend.config.rag import RAG_PDF_PRECHECK_PAGES, RAG_OCR_PROVIDER
            if (RAG_PDF_PRECHECK_PAGES > 0 and RAG_OCR_PROVIDER == "off"
                    and _pdf_has_text_layer(staging_path, RAG_PDF_PRECHECK_PAGES) is False):
                try:
                    os.unlink(staging_path)
                except OSError:
                    pass
                return {"ok": False, "error": (
                    "PDF 无文本层（可能为扫描件/纯图片），且 OCR 兜底未开启，无法解析；"
                    "请使用可复制文字的 PDF，或配置 RAG_OCR_PROVIDER 开启 OCR 支持"
                )}
        # B 阶段：源文件不再在此覆盖正式路径。was_overwrite 仅作响应语义
        # （提示这是一次覆盖上传），真正覆盖发生在发布协议的 os.replace。
        was_overwrite = os.path.isfile(final_path)
        _safe_put({"stage": "uploading", "progress": 100, "bytes": total})

        return {
            "ok": True,
            "upload_id": upload_id,
            "filepath": final_path,          # 逻辑正式路径（registry/幂等键）
            "staging_path": staging_path,    # 不可变暂存文件（Worker 只读它）
            "generation": generation,
            "filename": safe_name,
            "size": total,
            "source": _extract_source(request),
            "batch_id": request.headers.get("X-Batch-Id") or None,
            "upload_elapsed_ms": int((time.time() - _upload_t0_sync) * 1000),
            "was_overwrite": was_overwrite,
            "file_hash": file_hash,
            "tenant_id": tenant_id,
            "actor_id": actor_id,
            "idempotency_key": idempotency_key,
        }
    except Exception as e:
        if os.path.exists(staging_path):
            try:
                os.unlink(staging_path)
            except OSError as cleanup_error:
                logger.warning(f"[RAG] 暂存文件清理失败: {cleanup_error}")
        return {"ok": False, "error": f"upload failed: {type(e).__name__}: {e}"}


async def _finalize_upload_queue(upload_id: str) -> None:
    """P1-3：向队列放 None 哨兵（让 SSE 流结束）。

    三处分支（失败 / duplicate / 成功）的统一收尾。
    注意：这里【不】主动 pop 队列 —— 后台索引可能先于客户端订阅 SSE 完成
    （小文件场景），若提前 pop，晚到的订阅者会收到“upload_id 不存在或已过期”
    而丢失全部终态事件（done/error/duplicate）。保留队列 + None 哨兵后，
    晚到的订阅者仍可完整消费事件流；队列回收交给两条既有路径：
      1. SSE event_stream 断连时 pop（finally 块）；
      2. cleanup_expired_progress_queues 定时 GC（PROGRESS_QUEUE_TTL_SECONDS）
         兜底客户端从未订阅的残留。
    """
    queue = _progress_queues.get(upload_id)
    if queue is not None:
        try:
            await queue.put(None)
        except Exception as put_err:
            logger.warning(f"[RAG] queue.put(None) 失败 ({upload_id}): {put_err}")


def _cleanup_failed_upload_sync(filepath: str, was_overwrite: bool = False) -> None:
    """索引失败后的文件清理（同步核心，Celery Worker 侧复用）。

    B 阶段语义变更：filepath 现在传「暂存文件」——失败清理只删暂存，
    正式路径（data/docs/...）在任何失败分支都不被触碰（旧版可读契约）；
    「删除复活/覆盖丢源」两类历史缺陷在候选模型下结构性地不可能发生。
    was_overwrite 参数保留兼容旧调用方（不再影响行为）。
    """
    if not filepath:
        return
    try:
        if os.path.isfile(filepath):
            os.remove(filepath)
            logger.info(f"[RAG] 已清理索引失败暂存文件: {filepath}")
    except OSError as exc:
        logger.warning(f"[RAG] 索引失败暂存文件清理失败 {filepath}: {exc}")


async def _cleanup_failed_upload(filepath: str, was_overwrite: bool = False) -> None:
    """异步包装（兼容既有调用方）；实现见 _cleanup_failed_upload_sync。"""
    _cleanup_failed_upload_sync(filepath, was_overwrite=was_overwrite)


def _write_progress_redis(upload_id: str, stage: str, message: str = "",
                          owner: tuple[str, str] | None = None, **extra) -> None:
    """Phase 5: 将上传进度镜像写入 Redis Hash（跨实例可查）。失败不影响主流程。

    终态一次写（2026-10-01 B 阶段契约）：镜像已落终态（done/error/duplicate）
    后拒绝任何中间态回写——幂等重放/迟到的 uploading 事件不能把终态改回
    uploading（SSE 订阅者据此保证终态不倒退）。
    """
    try:
        from backend.infra.redis.client import get_redis
        r = get_redis()
        if r is None:
            return
        from backend.config.redis import REDIS_KEY_PREFIX
        key = f"{REDIS_KEY_PREFIX}upload:{upload_id}"
        import json as _json
        if stage not in _SSE_TERMINAL_STAGES:
            current = r.hget(key, "stage")
            if isinstance(current, bytes):
                current = current.decode("utf-8", "replace")
            if current in _SSE_TERMINAL_STAGES:
                logger.debug(
                    f"[RAG] 进度镜像已终态({current})，拒绝中间态回写: {upload_id}::{stage}")
                return
        mapping = {
            "stage": stage,
            "message": message,
            "updated_at": str(time.time()),
            "detail": _json.dumps(extra, ensure_ascii=False, default=str) if extra else "{}",
        }
        if owner:
            mapping["owner_tenant"] = owner[0]
            mapping["owner_actor"] = owner[1]
        r.hset(key, mapping=mapping)
        r.expire(key, 600)
    except Exception as e:
        # 至少留一条观测日志：Redis 故障静默吞掉会让进度镜像失效无从排查
        logger.debug(f"[RAG] 进度镜像写入 Redis 失败: {e}")


def _mark_registry_failed(filepath: str) -> None:
    """索引失败后把 registry 占位行标为 failed（启动恢复的状态依据）。

    _index_file 开始时写入的 parsing 占位行若不收口，会在每次启动时被
    sync() 反复重试永久失败文档；active 行（覆盖上传前的旧版本）不降级。
    """
    try:
        row = _get_registry().get_by_path(filepath)
        if row and row.get("status") not in ("active", "deleted"):
            _get_registry().update_status(filepath, "failed")
    except Exception as exc:
        logger.debug(f"[RAG] registry 失败状态标记异常（不影响主流程）: {exc}")


def _get_index_semaphore() -> asyncio.Semaphore:
    """索引并发信号量（懒创建）：模块导入时可能尚无事件循环，
    首次使用时创建，避免 loop 绑定兼容问题。"""
    global _index_semaphore
    if _index_semaphore is None:
        _index_semaphore = asyncio.Semaphore(_INDEX_CONCURRENCY_LIMIT)
    return _index_semaphore


def _settle_index_result(upload_id: str, filepath: str, filename: str, source: str,
                         batch_id: str | None, kb_id: str,
                         upload_elapsed_ms: int | None, was_overwrite: bool,
                         upload_t0: float, result: dict | None, emit_fn,
                         exc: BaseException | None = None,
                         staging_path: str = "") -> dict | None:
    """索引终态收口 —— API 进程内与 Celery Worker 两条路径的统一出口（阶段4）。

    emit_fn: 同步事件发射器 (stage, message, **extra)。
      - API 进程内模式 = 进程内 queue.put_nowait + Redis 镜像
      - Celery Worker 模式 = 仅 Redis 镜像（跨进程，SSE 轮询消费）
    exc: 非 None 走失败分支（失败清理只删暂存文件，正式路径不动——
    B 阶段候选模型的旧版可读契约）。
    队列 None 哨兵不在本函数处理 —— API 模式调用方负责 _finalize_upload_queue；
    Worker 模式无队列概念（SSE 由 Redis 轮询，见 stream_upload_progress）。
    """
    from backend.rag.progress_listener import ProgressListener

    # ---- 失败终态 ----
    if exc is not None:
        logger.error(f"[RAG] 后台索引失败: {exc}", exc_info=exc)
        # 运行记录终态（候选状态权威）。发布段失败（status=publishing，提交点
        # 已过）不标 failed——保持 publishing 等待重试续跑，不清理任何候选
        # 产物与暂存文件（续跑还要用）；仅候选期失败才走完整清理。
        run_status_now = ""
        try:
            from backend.rag.indexing.index_run_store_pg import (
                RunStateConflict, get_index_run_store,
            )
            _store = get_index_run_store()
            run_status_now = str(
                (_store.get_run(upload_id) or {}).get("status") or "")
            if run_status_now != "publishing":
                _store.mark_status(
                    upload_id, "failed", stage="settle", error=str(exc)[:500])
        except RunStateConflict:
            pass  # 已终态（幂等重放），保持一次写语义
        except Exception as run_err:
            logger.warning(f"[RAG] 运行记录 failed 标记失败: {run_err}")
        publishing_resume = run_status_now == "publishing"
        if not publishing_resume:
            # 候选期失败：暂存 + 候选产物整体清理（正式数据不在其中）
            if staging_path:
                _cleanup_failed_upload_sync(staging_path, was_overwrite=False)
            _cleanup_candidate_artifacts(upload_id, staging_path)
        # P1-4:ChunkingEmptyError 是业务失败(扫描件/结构损坏),保留源文件供排查;
        #      其它异常按孤儿文件处理逻辑清理
        from backend.rag.indexing.indexer import ChunkingEmptyError
        from backend.shared.error_protocol import (
            ErrorCode,
            ProtocolError,
            error_envelope_from_exception,
        )
        duration_ms = int((time.time() - upload_t0) * 1000) + (upload_elapsed_ms or 0)
        if isinstance(exc, ChunkingEmptyError):
            protocol_error = ProtocolError(
                ErrorCode.INVALID_PARAM,
                "文档内容无法解析，请检查文件后重试。",
                source="sse",
            )
            emit_fn(
                "error",
                protocol_error.envelope.message,
                error_type="chunking_empty",
                recoverable=True,
                error_protocol=protocol_error.envelope.to_dict(),
            )
            _safe_log_op("", filename, "upload", source, trace_id=None, batch_id=batch_id,
                         result="failed", duration_ms=duration_ms,
                         detail={"error": str(exc)[:200], "error_type": "chunking_empty"})
        elif isinstance(exc, FileLockedByOtherError):
            # P0-2:锁冲突 = 同文件另一请求正在索引。源文件绝不能删 ——
            # 持锁方（Celery Worker 或另一本机任务）可能正在读它，
            # 删除会让对方索引中途断源。标 recoverable 让前端提示可重试。
            protocol_error = ProtocolError(
                ErrorCode.IDEMPOTENCY_CONFLICT,
                "同文件正在被另一请求索引，请稍后重试。",
                source="sse",
            )
            emit_fn(
                "error",
                protocol_error.envelope.message,
                error_type="file_locked",
                recoverable=True,
                error_protocol=protocol_error.envelope.to_dict(),
            )
            _safe_log_op("", filename, "upload", source, trace_id=None, batch_id=batch_id,
                         result="failed", duration_ms=duration_ms,
                         detail={"error": str(exc)[:200], "error_type": "file_locked"})
        elif isinstance(exc, UploadIdempotencyConflict):
            protocol_error = ProtocolError(
                ErrorCode.IDEMPOTENCY_CONFLICT,
                str(exc)[:300],
                source="sse",
            )
            emit_fn(
                "error",
                protocol_error.envelope.message,
                error_type="idempotency_conflict",
                recoverable=False,
                error_protocol=protocol_error.envelope.to_dict(),
            )
            _safe_log_op("", filename, "upload", source, trace_id=None, batch_id=batch_id,
                         result="failed", duration_ms=duration_ms,
                         detail={"error": str(exc)[:200], "error_type": "idempotency_conflict"})
        elif isinstance(exc, SupersededCandidate):
            # 单发布者裁决：另一个（更新的）候选已发布，本次候选被取代。
            # 旧版本保持可读可检索——这是明确业务终态，不是系统故障。
            protocol_error = ProtocolError(
                ErrorCode.IDEMPOTENCY_CONFLICT,
                str(exc)[:300] or "该文档已被更新的版本抢先发布，本次上传被取代。",
                source="sse",
            )
            emit_fn(
                "error",
                protocol_error.envelope.message,
                error_type="superseded",
                recoverable=False,
                error_protocol=protocol_error.envelope.to_dict(),
            )
            _safe_log_op("", filename, "upload", source, trace_id=None, batch_id=batch_id,
                         result="failed", duration_ms=duration_ms,
                         detail={"error": str(exc)[:200], "error_type": "superseded"})
        else:
            envelope = error_envelope_from_exception(exc, source="sse")
            emit_fn("error", envelope.message,
                    error_protocol=envelope.to_dict())
            _safe_log_op("", filename, "upload", source, trace_id=None, batch_id=batch_id,
                         result="failed", duration_ms=duration_ms,
                         detail={"error": str(exc)[:200]})
        return None

    result = result or {}
    terminal = result.get("terminal", "done")
    # span_id → 前端 stage 键的统一映射（终态阶段耗时使用同一规则）
    _SPAN_STAGE_KEY = ProgressListener.SPAN_STAGE_KEY

    # ---- duplicate 终态 ----
    if terminal == "duplicate":
        duplicate_doc = result.get("doc") or {}
        stage_elapsed = result.get("stage_elapsed") or {}
        # 用真实上传耗时覆盖（duplicate 跳过索引，后端算的 uploading 没意义）
        if upload_elapsed_ms is not None:
            stage_elapsed["uploading"] = upload_elapsed_ms
        total_ms = (upload_elapsed_ms or 0) + int((time.time() - upload_t0) * 1000)
        emit_fn("duplicate", "文件已存在，未重复索引",
                doc=sanitize_doc_row(duplicate_doc), trace_id="", stage_elapsed=stage_elapsed, total_ms=total_ms,
                processing_run_id=result.get("processing_run_id", ""),
                model_summary=result.get("model_summary", []))
        _safe_log_op(
            duplicate_doc.get("doc_id", ""), filename, "upload", source,
            trace_id="", batch_id=batch_id, result="duplicate",
            duration_ms=total_ms,
            detail={"duplicate": True, "chunk_count": duplicate_doc.get("chunk_count", 0)},
        )
        return

    # ---- 成功终态：按 path 直接拿刚索引的文档 ----
    new_doc = None
    try:
        reg = _get_registry()
        new_doc = reg.get_by_path(filepath)
        # 终态携带完整阶段耗时（来自后端 span duration_ms），覆盖前端累加
        raw_elapsed = result.get("stage_elapsed") or {}
        # span_id 转为前端 stage 键（如 index_chunk → chunking）
        stage_elapsed: dict[str, int] = {}
        for sid, ms in raw_elapsed.items():
            stage_key = _SPAN_STAGE_KEY.get(sid, sid)
            stage_elapsed[stage_key] = int(ms)
        # 优先用 sync_upload_impl 实测的上传耗时；缺失时回退到减法逻辑（向后兼容）
        index_elapsed_ms = int((time.time() - upload_t0) * 1000)
        total_ms = index_elapsed_ms + (upload_elapsed_ms or 0)
        if upload_elapsed_ms is not None:
            stage_elapsed["uploading"] = upload_elapsed_ms
        elif "uploading" not in stage_elapsed:
            others = sum(v for k, v in stage_elapsed.items() if k != "uploading")
            stage_elapsed["uploading"] = max(total_ms - others, 0)
        # D-2 口径：形参 was_overwrite（os.replace 前一刻 isfile 判定，
        # 由调用方从 _do_index_sync 的 result 对齐）为唯一事实源；
        # result 内字段缺省时回退形参（两条路径都不断链）
        emit_fn("done", "索引完成", doc=sanitize_doc_row(new_doc),
                trace_id=result.get("trace_id") or "",
                processing_run_id=result.get("processing_run_id") or "",
                model_summary=result.get("model_summary") or [],
                was_overwrite=bool(result.get("was_overwrite", was_overwrite)),
                stage_elapsed=stage_elapsed,
                total_ms=total_ms)
        # Phase 4: 文档变更后失效该 KB 的答案缓存（避免返回过时答案）
        try:
            from backend.rag.answer_cache import get_answer_cache
            get_answer_cache().invalidate_kb(kb_id)
        except Exception as cache_err:
            logger.debug(f"[RAG] 答案缓存失效失败（非致命）: {cache_err}")
    except Exception as e:
        emit_fn("done", "索引完成（文档信息获取失败）")
        logger.warning(f"[RAG] 获取入库文档信息失败: {e}")

    _safe_log_op(
        (new_doc or {}).get("doc_id", ""), filename, "upload", source,
        trace_id=result.get("trace_id") or None,
        batch_id=batch_id, result="success",
        duration_ms=int((time.time() - upload_t0) * 1000),
        detail={
            "chunk_count": result.get("chunk_count", 0),
            "file_hash": result.get("file_hash", ""),
            "duplicate": False,
            "processing_run_id": result.get("processing_run_id", ""),
            "doc_type": (new_doc or {}).get("doc_type", "general"),
            "llm_used": bool((new_doc or {}).get("llm_used", False)),
            "confidence": (new_doc or {}).get("confidence", 0),
        },
    )


def _dispatch_index_to_celery(**kwargs) -> dict:
    """上传索引任务入队 Celery（rag_index 队列）。broker 不可达时抛异常。

    独立成函数便于测试注入：链路 e2e 测试用 autouse fixture 把它替换为
    抛 ConnectionError，即模拟 broker 不可达 → 覆盖进程内回退路径
    （celery 队列化主路径由 test_rag_upload_celery_mode.py 单独覆盖）。

    Phase1 Step8 试点：入队前创建统一 tasks 行（TaskState 接入，失败降级
    不阻断索引）。kwargs 中的 actor_id/tenant_id 仅用于任务归属，不进
    Celery 消息（索引任务签名不感知身份）。

    Phase2 Step3：queue 由 QueueRouter 按 workflow binding 决定（收口，
    不再直接读 CELERY_RAG_INDEX_QUEUE）。

    B 阶段：broker 投递前先落 rag_index_runs 运行记录（候选状态权威，
    记录 base_generation 发布基准与暂存文件位置）——崩溃恢复 sweeper 重投
    时才有续跑依据。
    """
    from backend.services import task_service
    from backend.tasks.index_task_runtime import create_index_task_record
    from backend.tasks.index_tasks import execute_index_task
    from backend.tasks.queue_router import log_route, resolve_for_workflow

    actor_id = str(kwargs.pop("actor_id", "") or "")
    tenant_id = str(kwargs.pop("tenant_id", "") or "")
    generation = str(kwargs.get("generation", "") or "")
    staging_path = str(kwargs.get("staging_path", "") or "")
    file_hash = str(kwargs.get("file_hash", "") or "")
    logical_path = str(kwargs.get("filepath", "") or "")
    if generation:
        from backend.rag.indexing.index_run_store_pg import get_index_run_store
        existing_row = _get_registry().get_by_path(logical_path)
        get_index_run_store().create_run(
            upload_id=str(kwargs.get("upload_id", "")),
            generation=generation,
            file_path=logical_path,
            kb_id=str(kwargs.get("kb_id", "") or ""),
            department=str(kwargs.get("department", "") or ""),
            file_hash=file_hash,
            staging_path=staging_path,
            tenant_id=tenant_id,
            actor_id=actor_id,
            base_generation=str(
                (existing_row or {}).get("active_generation") or ""),
        )

    db_task_id = create_index_task_record(
        kwargs.get("upload_id", ""), kwargs.get("filename", ""),
        kb_id=kwargs.get("kb_id", "") or "policy_general",
        tenant_id=tenant_id or "default",
        user_id=actor_id or "system",
        index_kwargs=dict(kwargs))
    if db_task_id:
        kwargs = {**kwargs, "db_task_id": db_task_id}

    route = resolve_for_workflow("rag_index")
    async_result = execute_index_task.apply_async(
        kwargs=kwargs, queue=route.physical_queue
    )
    if db_task_id:
        task_service.mark_queued(db_task_id,
                                 getattr(async_result, "id", ""),
                                 queue=route.physical_queue)
        log_route(route, dispatch_type="initial", task_id=db_task_id,
                  celery_task_name="tasks.execute_index")
    return {"queued": True, "celery_task_id": getattr(async_result, "id", ""),
            "db_task_id": db_task_id,
            "upload_id": str(kwargs.get("upload_id", ""))}


def _dispatch_index_with_idempotency(
    *,
    task_kwargs: dict,
    file_hash: str,
    tenant_id: str,
    actor_id: str,
    idempotency_key: str,
) -> dict:
    """提交 RAG 索引任务；可信请求拒绝绕过幂等边界。

    幂等语义（B 阶段收口）：
      - 同键同内容重放 → 返回同一任务的派发结果（SSE 续看同一进度镜像）；
      - 同键不同内容 → UploadIdempotencyConflict（HTTP 层 409），
        已存在任务与正式版本均不受影响；
      - 同键同内容但在途 → 附着到在途任务（不重复派发）。
    幂等指纹只含逻辑身份（正式路径/文件名/内容哈希/KB/部门），不含
    暂存文件 nonce——同内容重试必然命中同一条目。
    """
    if not tenant_id or not actor_id:
        return _dispatch_index_to_celery(**task_kwargs)

    from backend.shared.idempotency import (
        IdempotencyConflict,
        run_idempotent_operation_for_identity,
    )

    payload = {
        "filepath": task_kwargs["filepath"],
        "filename": task_kwargs["filename"],
        "file_hash": file_hash,
        "kb_id": task_kwargs.get("kb_id", ""),
        "department": task_kwargs.get("department", ""),
    }
    upload_id = str(task_kwargs.get("upload_id", ""))

    def _on_conflict() -> dict:
        """同键冲突：分辨「同内容在途（附着）」与「异内容（409 拒绝）」。"""
        from backend.rag.indexing.index_run_store_pg import get_index_run_store
        run = get_index_run_store().get_run(upload_id)
        if (run and run.get("file_hash") == file_hash
                and run.get("kb_id") == task_kwargs.get("kb_id", "")
                and run.get("department") == task_kwargs.get("department", "")):
            return {"queued": False, "attached": True, "upload_id": upload_id,
                    "db_task_id": run.get("celery_task_id", "")}
        raise UploadIdempotencyConflict(
            "同一幂等键已绑定不同内容（或在途任务内容不一致），"
            "请更换 Idempotency-Key 或等待既有任务完成",
            upload_id=upload_id,
        )

    try:
        result = run_idempotent_operation_for_identity(
            "rag.index.submit",
            payload,
            lambda: _dispatch_index_to_celery(**task_kwargs),
            tenant_id=tenant_id,
            actor_id=actor_id,
            client_key=idempotency_key,
            # 缓存 Redis 停止时，上传仍通过 PG durable ledger 保证只提交
            # 一次；不允许退化为进程内幂等，主链只损失缓存能力。
            allow_postgres_fallback=True,
        )
    except ValueError as ve:
        # PG 结果仓储以裸 ValueError("IDEMPOTENCY_CONFLICT") 表达同键不同内容
        if "IDEMPOTENCY_CONFLICT" not in str(ve):
            raise
        result = _on_conflict()
    except IdempotencyConflict:
        result = _on_conflict()
    return _resolve_replayed_dispatch(result, task_kwargs, file_hash)


def _resolve_replayed_dispatch(result: dict, task_kwargs: dict, file_hash: str) -> dict:
    """幂等重放守卫（联合验收实测缺口）：重放结果可能是「死任务」。

    场景：同指纹（同内容）重传 → 提交层 ledger 直接重放上次派发结果，
    但上次任务可能已终态失败（worker 重试耗尽/解析失败）。此时必须
    重新派发新一轮候选（create_run 重置状态 + 新 generation），否则
    SSE 永远等不到终态。分三种情形：
      - 重放任务的运行记录仍活着 → 原样返回（幂等语义）；
      - 重放任务已终态失败 → 重新派发（新任务）；
      - 重放结果属于【另一条】上传（同内容更早的请求）→ 附着到那条
        任务（调用方以返回的 upload_id 订阅 SSE）；它已失败则重新派发。
    """
    from backend.rag.indexing.index_run_store_pg import get_index_run_store

    result = dict(result or {})
    replayed_id = str(result.get("upload_id", ""))
    current_id = str(task_kwargs.get("upload_id", ""))
    if not replayed_id:
        return result  # 旧格式结果（无归属信息），保持旧行为
    run_store = get_index_run_store()
    run = run_store.get_run(replayed_id)
    if run is None:
        return result  # 运行记录缺失（历史数据）——保持旧行为
    status = str(run.get("status") or "")
    if status not in ("failed", "superseded"):
        if replayed_id != current_id:
            return {"queued": False, "attached": True,
                    "upload_id": replayed_id,
                    "run_status": status,
                    "db_task_id": result.get("db_task_id", "")}
        return result
    logger.warning(
        "[RAG] 幂等重放命中已失败任务(%s)，重新派发新一轮候选: %s",
        status, current_id or replayed_id)
    fresh = _dispatch_index_to_celery(**task_kwargs)
    return fresh


async def _run_index_background(upload_id: str, filepath: str, filename: str, source: str = "", batch_id: str | None = None, kb_id: str = "policy_general", department: str = "general", upload_elapsed_ms: int | None = None, was_overwrite: bool = False, file_hash: str = "", tenant_id: str = "", actor_id: str = "", idempotency_key: str = "", staging_path: str = "", generation: str = "", pre_dispatched: dict | None = None, task_kwargs: dict | None = None):
    """后台执行索引，向 queue 推送阶段事件；完成后记录操作日志。

    B 阶段：可信请求的 Celery 派发已在 HTTP 请求路径内完成（pre_dispatched
    非 None），本任务只负责「已入队」进度事件与 SSE 通道路由标记；不可信
    请求（task_kwargs 非 None）沿用派发+回退逻辑。

    was_overwrite: 本次上传是否将覆盖已有同名文件（仅响应语义；真正的
    覆盖发生在发布协议的 os.replace，失败时正式文件不被触碰）。
    """
    queue = _progress_queues.get(upload_id)
    if queue is None:
        await _cleanup_failed_upload(staging_path or filepath, was_overwrite=was_overwrite)
        return

    async def emit(stage: str, message: str = "", **extra):
        await queue.put({"stage": stage, "message": message, **extra})
        # Redis 写盘是同步网络 IO，放线程池执行，避免 Redis 慢时阻塞事件循环
        await asyncio.to_thread(_write_progress_redis, upload_id, stage, message,
                                owner=(tenant_id, actor_id), **extra)

    # 同步发射器：终态收口（_settle_index_result）与 Celery 分流共用。
    # queue.put_nowait 对无界 asyncio.Queue 与 await put 语义一致。
    def emit_fn(stage: str, message: str = "", **extra):
        if queue is not None:
            queue.put_nowait({"stage": stage, "message": message, **extra})
        _write_progress_redis(upload_id, stage, message,
                              owner=(tenant_id, actor_id), **extra)

    _upload_t0 = time.time()

    # ── 可信请求：派发已在请求路径完成 ──
    if pre_dispatched is not None:
        # 打标必须在发任何事件之前：SSE 队列模式每轮检查此标记，
        # 看到即切换 Redis 轮询通道消费 Worker 事件（跨进程队列收不到）
        _celery_routed.add(upload_id)
        await emit("uploading", f"文件 {filename} 已保存，索引任务已入队（Celery Worker 执行）")
        # 终态由 Worker 写 Redis 进度镜像；本进程队列不再有后续事件
        return

    # ── 不可信请求：派发 + broker 不可达回退本进程索引 ──
    if task_kwargs is None:
        # 旧式直调（无请求路径派发）：从函数参数装配完整任务字段
        task_kwargs = {
            "upload_id": upload_id, "filepath": filepath,
            "filename": filename, "kb_id": kb_id, "department": department,
            "source": source, "batch_id": batch_id,
            "upload_elapsed_ms": upload_elapsed_ms,
            "actor_id": actor_id, "tenant_id": tenant_id,
            "was_overwrite": was_overwrite,
        }
    task_kwargs.setdefault("staging_path", staging_path)
    task_kwargs.setdefault("generation", generation)
    task_kwargs.setdefault("file_hash", file_hash)
    try:
        _dispatch_index_with_idempotency(
            task_kwargs=task_kwargs,
            file_hash=file_hash,
            tenant_id=tenant_id,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
        )
        _celery_routed.add(upload_id)
        await emit("uploading", f"文件 {filename} 已保存，索引任务已入队（Celery Worker 执行）")
        return
    except Exception as enqueue_err:
        if tenant_id and actor_id:
            # 可信请求不能在幂等 Redis/PG 或任务提交失败时静默降级到
            # 进程内执行，否则会绕过“只执行一次”保证。
            _settle_index_result(
                upload_id, filepath, filename, source, batch_id, kb_id,
                upload_elapsed_ms, was_overwrite, _upload_t0,
                result=None, emit_fn=emit_fn, exc=enqueue_err,
                staging_path=staging_path)
            await _finalize_upload_queue(upload_id)
            return
        # broker 不可达：可用性优先，回退本进程索引（与 task_manager 503 语义对齐）
        logger.warning(f"[RAG] Celery 入队失败，回退进程内索引: {enqueue_err}")
        _celery_routed.discard(upload_id)
        await emit("uploading", "索引队列暂不可用，已切换为本机索引")

    result = None
    try:
        await emit("uploading", f"文件 {filename} 已保存，开始索引")

        # 同步索引（在线程池跑，不阻塞事件循环）；返回含 trace_id
        loop = asyncio.get_running_loop()
        # 并发闸门：解析/chunks/向量都驻留单任务内存，限制同时索引数防
        # 内存峰值叠加；槽位满时提示排队（emit 用 uploading 阶段，前端无感）
        sem = _get_index_semaphore()
        if sem.locked():
            await emit("uploading",
                       f"索引队列繁忙（最大并发 {_INDEX_CONCURRENCY_LIMIT}），排队等待中...")
        async with sem:
            from functools import partial
            result = await loop.run_in_executor(
                None,
                partial(
                    _do_index_sync,
                    upload_id, filepath, filename, loop, kb_id, department,
                    batch_id=batch_id,
                    staging_path=staging_path, generation=generation,
                    file_hash=file_hash,
                ),
            )
    except FileLockedByOtherError:
        # P0-2 双重投递兜底：Celery 任务实际已入队（broker 响应丢失被误判为
        # 入队失败）+ 本机回退同时执行，本机抢锁失败即此场景。
        # 索引由 Worker 负责 —— 这里绝不能走失败收口（旧实现会
        # _cleanup_failed_upload_sync 删掉 Worker 正在索引的源文件，导致
        # Worker 中途断源）。改为打标切换 SSE 到 Redis 轮询通道，消费
        # Worker 的终态事件；若 Worker 任务实际不存在（极罕见的跨上传
        # 锁冲突），SSE 空转后由轮询超时兜底报错，源文件保持原样。
        logger.warning(
            f"[RAG] {filename} 文件锁被占（Celery 任务大概率已在执行），本机回退退出")
        _celery_routed.add(upload_id)
        await emit("uploading", "索引任务已在队列中执行，等待其结果...")
        return
    except Exception as e:
        _settle_index_result(
            upload_id, filepath, filename, source, batch_id, kb_id,
            upload_elapsed_ms, was_overwrite, _upload_t0,
            result=None, emit_fn=emit_fn, exc=e,
            staging_path=staging_path)
        await _finalize_upload_queue(upload_id)
        return

    _settle_index_result(
        upload_id, filepath, filename, source, batch_id, kb_id,
        upload_elapsed_ms, was_overwrite, _upload_t0,
        result=result, emit_fn=emit_fn)
    await _finalize_upload_queue(upload_id)

def _remove_bak(filepath: str) -> None:
    """索引成功终态清理覆盖上传的 .bak 备份。

    失败分支不调用 — 索引失败时 .bak 保留,旧版本可人工恢复。
    """
    bak = filepath + ".bak"
    try:
        if os.path.isfile(bak):
            os.remove(bak)
    except OSError as exc:
        logger.warning(f"[RAG] .bak 清理失败 {bak}: {exc}")


def _do_index_sync(upload_id: str, filepath: str, filename: str,
                   main_loop: asyncio.AbstractEventLoop | None,
                   kb_id: str = "policy_general",
                   department: str = "general",
                   batch_id: str | None = None,
                   staging_path: str = "",
                   generation: str = "",
                   file_hash: str = ""):
    """候选模式索引（B 阶段）：解析→向量→BM25 全部落在 generation 隔离层，
    发布协议统一提交；任何阶段失败旧版保持可读可检索。

    关键语义：
      - Worker 只读不可变暂存文件（staging_path），正式路径在发布前不被触碰；
      - registry 在候选期只读（不再有 parsing 占位原地降级）；
      - 发布拿索引锁串行化——锁只保护发布段，两个并发候选可并行解析/向量，
        由发布 CAS 裁决唯一发布者，输者明确 superseded；
      - 终态幂等：published 运行重入直接返回 done（不重复发布）。

    main_loop 传 None：Worker 进程无 asyncio 主循环，sync_emit 只写 Redis。
    """
    from backend.config import DOCS_DIRECTORY

    queue = _progress_queues.get(upload_id)

    _upload_t0_sync = time.time()

    def sync_emit(stage: str, message: str = "", **extra):
        """从同步线程调用：进程内队列事件投主 loop（仅 API 回退路径有）"""
        _write_progress_redis(upload_id, stage, message, **extra)
        if queue is None or main_loop is None:
            return  # Worker / 无订阅者 → 只写 Redis 镜像
        evt = {"stage": stage, "message": message, **extra}
        asyncio.run_coroutine_threadsafe(queue.put(evt), main_loop)

    reg = _get_registry()
    from backend.rag.indexing.index_run_store_pg import (
        RunStateConflict,
        get_index_run_store,
    )
    run_store = get_index_run_store()
    run = run_store.get_run(upload_id)
    if run is None:
        # 旧消息/回退路径现场登记（base 取当前 registry 指针）
        generation = generation or uuid.uuid4().hex[:16]
        existing0 = reg.get_by_path(filepath)
        run = run_store.create_run(
            upload_id=upload_id, generation=generation, file_path=filepath,
            kb_id=kb_id, department=department,
            file_hash=file_hash, staging_path=staging_path,
            base_generation=str((existing0 or {}).get("active_generation") or ""),
        )
    generation = run["generation"]
    staging = run.get("staging_path") or staging_path or filepath
    source_hash = file_hash or run.get("file_hash") or ""

    # ── 终态幂等短路（重试不得重复发布）──
    if run["status"] == "published":
        doc = reg.get_by_path(filepath) or {}
        logger.info(f"[RAG] 运行已发布，幂等返回 done: {upload_id}")
        return {
            "trace_id": "", "terminal": "done",
            "doc": {**doc, "duplicate": False},
            "chunk_count": int(doc.get("chunk_count") or 0),
            "file_hash": doc.get("file_hash", source_hash),
            "stage_elapsed": {},
        }
    if run["status"] in ("failed", "superseded"):
        # 失败终态被重投（sweeper 唤醒）：不复活失败任务；正在的新一轮
        # 上传会经 create_run 重置状态走正常流程
        raise RuntimeError(
            f"索引运行已终态({run['status']})，拒绝重跑: {run.get('error', '')[:200]}")

    if not os.path.isfile(staging):
        raise RuntimeError(f"暂存源文件缺失（可能已被清理）: {os.path.basename(staging)}")

    # 哈希校验：上传源哈希必须等于 Worker 处理的哈希（B 阶段验收项）
    actual_hash = sha256_of_file(staging)
    if source_hash and actual_hash != source_hash:
        raise RuntimeError(
            "暂存文件哈希与任务声明不一致（源文件被篡改或写坏），拒绝索引")

    sync_emit("uploading", "正在校验文档版本...", file_hash=actual_hash[:12])

    # ── duplicate 检测（与 active 版本同内容 → 无需发布）──
    existing = reg.get_by_path(filepath)
    if existing and existing.get("status") == "active" \
            and existing.get("file_hash") == actual_hash:
        logger.info(f"[RAG] 文件未变化，跳过索引: {filename}")
        run_store.mark_status(upload_id, "published", stage="duplicate")
        return {
            "trace_id": "",
            "terminal": "duplicate",
            "doc": {**existing, "duplicate": True},
            "file_hash": actual_hash,
            "stage_elapsed": {"uploading": int((time.time() - _upload_t0_sync) * 1000)},
        }

    # ── 同逻辑文档互斥：已有其他运行在途 → 明确拒绝（排队语义交上层重试）──
    others = [r for r in run_store.list_non_terminal_by_file(filepath)
              if r.get("upload_id") != upload_id]
    if others:
        raise FileLockedByOtherError(
            f"文档 {filename} 已有另一个上传任务在处理（{others[0].get('upload_id')}），请稍后重试")

    sync_emit("uploading", "正在初始化索引管道（首次 ~15s）...")
    _pipe_t0 = time.time()
    pipeline = get_rag_pipeline()
    _pipe_elapsed = int((time.time() - _pipe_t0) * 1000)
    if _pipe_elapsed > 3000:
        logger.info(f"[RAG] 管道初始化耗时 {_pipe_elapsed}ms（可能预热未完成）")

    # ── 候选期（无锁）：解析/向量/BM25 全在 generation 隔离层 ──
    from backend.rag.indexing.publish import (
        CandidatePublishError,
        SupersededCandidate,
        cleanup_candidate,
        make_candidate_stores,
    )
    stores = make_candidate_stores(pipeline, generation)
    base_generation = str(run.get("base_generation") or "")
    run_store.mark_status(upload_id, "indexing", stage="index")
    listener = ProgressListener(sync_emit)
    from backend.rag.indexing.indexer import IncrementalIndexer
    from backend.rag.indexing.processing_lineage_pg import (
        get_processing_lineage_repository,
    )
    indexer = IncrementalIndexer(
        docs_dir=DOCS_DIRECTORY,
        vectordb=stores.vectordb,
        doc_db=stores.doc_db,
        embedding=pipeline.embedding,
        registry=reg,
        kb_id=kb_id,
        department=department,
        bm25_store=stores.bm25_store,
        chunk_store=stores.chunk_store,
        candidate_mode=True,
        bm25_source_vectordb=stores.bm25_source,
        processing_lineage_repository=get_processing_lineage_repository(),
        processing_task_id=upload_id,
        processing_batch_id=batch_id,
    )
    try:
        index_result = indexer.index_candidate(
            staging, filepath, file_hash=actual_hash)
    except SupersededCandidate:
        raise
    except Exception:
        # 候选期失败：候选层整体清理后原样上抛（终态收口统一发 error、
        # 标 failed、删暂存；正式数据未被触碰）
        cleanup_candidate(stores)
        raise
    finally:
        listener.unsub()
    index_result["staging_path"] = staging

    # ── 发布段（索引锁串行化；锁只保护发布段，候选期不占锁）──
    sync_emit("embedding", "候选索引完成，正在发布...")
    lock_fd = acquire_index_lock(filepath)
    _heartbeat_stop = threading.Event()
    _start_lock_heartbeat(filepath + ".lock", _heartbeat_stop)
    try:
        old_row = reg.get_by_path(filepath)
        try:
            _raw_old = (old_row or {}).get("chunk_ids") or "[]"
            _parsed_old = json.loads(_raw_old) if isinstance(_raw_old, str) else _raw_old
            old_chunk_ids = [str(x) for x in (_parsed_old or [])]
        except (ValueError, TypeError):
            old_chunk_ids = []
        from backend.rag.indexing.publish import publish_candidate
        publish_candidate(
            run_store=run_store,
            upload_id=upload_id,
            registry=reg,
            stores=stores,
            final_path=filepath,
            doc_id=index_result["doc_id"],
            kb_id=kb_id,
            file_hash=actual_hash,
            base_generation=base_generation,
            index_result=index_result,
            old_row=old_row,
            old_chunk_ids=old_chunk_ids,
            old_doc_db_id=str((old_row or {}).get("doc_db_id") or ""),
            main_bm25_store=pipeline.bm25_store,
        )
    except SupersededCandidate as s:
        # 单发布者裁决：更新的候选已发布。清理本次候选 + 暂存，明确报
        # superseded（旧版本保持可读可检索）
        cleanup_candidate(stores)
        try:
            run_store.mark_status(upload_id, "superseded", error=str(s)[:500])
        except RunStateConflict:
            pass
        if staging != filepath and os.path.isfile(staging):
            try:
                os.unlink(staging)
            except OSError:
                pass
        logger.warning(f"[RAG] 候选被取代: {filename}: {s}")
        raise
    except CandidatePublishError as e:
        # 提交点之后的失败：发布已提交（registry 已指向本代次），候选已晋级。
        # 保持 publishing 状态等待重试续跑（publish_candidate 幂等），不清理
        # 任何产物、不删暂存（续跑还要用它），不标记 failed。
        logger.error(f"[RAG] 发布段失败（保持 publishing 待续跑）: {e}")
        raise
    finally:
        _heartbeat_stop.set()
        release_index_lock(lock_fd, filepath)

    # 上传后刷新本进程内存检索对象（跨进程刷新由 C 阶段热刷新承担）
    try:
        pipeline.refresh_bm25_from_store()
    except Exception as e:
        logger.warning(f"[RAG] BM25 刷新失败（不影响索引结果）: {e}")
    # 发布通知（C 阶段）：pub/sub 推给在线 API/rag-service 立即刷新；
    # epoch 键留给错过消息的进程轮询兜底。通知失败不影响发布结果。
    try:
        from backend.infra.redis.client import get_redis
        from backend.config.redis import REDIS_KEY_PREFIX
        r = get_redis()
        if r is not None:
            r.publish(f"{REDIS_KEY_PREFIX}rag:index:published", generation)
            r.set(f"{REDIS_KEY_PREFIX}rag:index:generation", generation, ex=86400)
    except Exception as notify_err:  # noqa: BLE001 — 通知是加速项不是正确性依赖
        logger.debug(f"[RAG] 索引发布通知失败（版本检查兜底）: {notify_err}")

    logger.info(f"[RAG] 上传索引完成: {filename} → {index_result.get('doc_id')}")
    return {
        "trace_id": index_result.get("trace_id", ""),
        "chunk_count": index_result.get("chunk_count", 0),
        "file_hash": actual_hash,
        "doc_id": index_result.get("doc_id", ""),
        "processing_run_id": index_result.get("processing_run_id", ""),
        "stage_elapsed": index_result.get("stage_elapsed", {}),
    }




# ── 阶段4：Celery 模式下的 SSE Redis 轮询通道 ──────────────────
# 进度权威 = Redis Hash {REDIS_KEY_PREFIX}upload:{upload_id}
# （_write_progress_redis 镜像，跨进程可查）。Worker 与 API 分属不同
# 进程，进程内队列里没有 Worker 的事件，SSE 必须改为轮询 Redis。
_SSE_REDIS_POLL_SECONDS = RAG_SSE_REDIS_POLL_SECONDS
_SSE_REDIS_POLL_MAX_SECONDS = RAG_SSE_REDIS_POLL_MAX_SECONDS
_SSE_REDIS_EMPTY_GRACE_POLLS = 20   # 连续无镜像判定过期（20 × 0.5s = 10s）
_SSE_TERMINAL_STAGES = ("done", "error", "duplicate")
_SSE_KEEPALIVE_EVERY_N_POLLS = 60   # 无变化时每 30s 一条 keepalive

# 已成功入队 Celery 的 upload_id（进程内标记）。SSE 通道以此做上传级路由：
# 命中 → Redis 轮询通道（Worker 在另一进程，进程内队列没有它的事件）；
# 未命中 → 进程内队列模式（含入队失败回退本机索引的场景）。
# 队列模式消费循环每轮复查该标记，入队成功后无缝切换，无路由竞态。
_celery_routed: set = set()


def _read_progress_redis(upload_id: str) -> dict | None:
    """读 Redis 进度镜像并还原为事件 dict（{stage, message, **extra}）。

    Redis 不可用 / 键不存在返回 None（调用方区分「暂时还没有」与「过期」
    靠连续空轮询计数，不靠单次 None）。
    """
    import json as _json
    try:
        from backend.infra.redis.client import get_redis
        r = get_redis()
        if r is None:
            return None
        from backend.config.redis import REDIS_KEY_PREFIX
        data = r.hgetall(f"{REDIS_KEY_PREFIX}upload:{upload_id}")
        if not data:
            return None
        evt: dict = {"stage": data.get("stage", ""),
                     "message": data.get("message", "")}
        try:
            detail = _json.loads(data.get("detail") or "{}")
            if isinstance(detail, dict):
                evt.update(detail)
        except Exception:
            pass
        return evt
    except Exception:
        return None


async def _redis_poll_events(upload_id: str, last_sig: str | None = None):
    """Celery 模式 SSE 事件源：轮询 Redis 进度镜像直到终态（async 生成器）。

    last_sig: 切换通道前已推送的最后一条事件签名，避免跨通道重复推送。
    事件格式与队列模式完全一致（_sse_encode("message", evt)），前端零感知。
    """
    empty_polls = 0
    unchanged_polls = 0
    deadline = asyncio.get_running_loop().time() + _SSE_REDIS_POLL_MAX_SECONDS
    while True:
        evt = await asyncio.to_thread(_read_progress_redis, upload_id)
        if evt is None:
            empty_polls += 1
            if empty_polls >= _SSE_REDIS_EMPTY_GRACE_POLLS:
                yield _sse_encode("error", {"message": f"upload_id {upload_id} 不存在或已过期"})
                return
            await asyncio.sleep(_SSE_REDIS_POLL_SECONDS)
            continue
        empty_polls = 0
        import json as _json
        sig = _json.dumps(evt, sort_keys=True, ensure_ascii=False, default=str)
        if sig != last_sig:
            last_sig = sig
            unchanged_polls = 0
            yield _sse_encode("message", evt)
            if evt.get("stage") in _SSE_TERMINAL_STAGES:
                return
        else:
            unchanged_polls += 1
            if unchanged_polls >= _SSE_KEEPALIVE_EVERY_N_POLLS:
                unchanged_polls = 0
                yield ": keepalive\n\n"
        if asyncio.get_running_loop().time() > deadline:
            yield _sse_encode("error", {"message": "索引进度轮询超时（任务可能仍在后台执行）"})
            return
        await asyncio.sleep(_SSE_REDIS_POLL_SECONDS)


def _redis_poll_stream_response(upload_id: str, last_sig: str | None = None):
    """Celery 模式 SSE：包装 _redis_poll_events 为 StreamingResponse。"""
    async def event_stream():
        async for chunk in _redis_poll_events(upload_id, last_sig=last_sig):
            yield chunk

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def _progress_owner_of(upload_id: str) -> tuple[str, str]:
    """upload_id → 上传者 (tenant_id, actor_id)：进程内注册表优先，
    跨实例（本进程没登记）回退读 Redis 镜像里的 owner 字段。"""
    owner = _progress_owners.get(upload_id)
    if owner:
        return owner
    import json as _json
    try:
        from backend.infra.redis.client import get_redis
        r = get_redis()
        if r is None:
            return ("", "")
        from backend.config.redis import REDIS_KEY_PREFIX
        data = r.hgetall(f"{REDIS_KEY_PREFIX}upload:{upload_id}") or {}
        tenant = data.get("owner_tenant") or ""
        actor = data.get("owner_actor") or ""
        if isinstance(tenant, bytes):
            tenant = tenant.decode("utf-8", "replace")
        if isinstance(actor, bytes):
            actor = actor.decode("utf-8", "replace")
        return (tenant, actor)
    except Exception:
        return ("", "")


def _ensure_progress_owner(request: Request, upload_id: str) -> bool:
    """SSE 订阅归属校验（2026-10-01 权限收口）：只允许上传者本人或 admin。

    upload_id 此前可被推断/猜测（确定性 sha256），无归属校验 = 任何已认证
    用户可窃听他人上传进度与终态 doc 元数据。校验失败返回 False，调用方
    以与「任务不存在」完全相同的响应拒绝（不泄露存在性）。
    """
    principal = require_principal(request)
    tenant, actor = _progress_owner_of(upload_id)
    if not tenant and not actor:
        return False  # 无归属记录（过期/不可信请求）→ 一律拒绝
    if tenant and principal.tenant_id == tenant and principal.user_id == actor:
        return True
    try:
        return RagAuthorization.build(principal).is_admin
    except RagAuthorizationError:
        return False


@router.get("/upload-failures", dependencies=[Depends(require_rag_editor)])
async def list_upload_failures(limit: int = 50):
    """待处理入库失败清单（质量门禁/解析 0 分块/向量化终态等异步失败）。

    上传受理后索引在 worker 异步执行，失败发生在对话框关闭之后——此端点是
    管理端「入库失败」信号（仪表盘待处理卡片 + 失败列表页）的唯一数据出口。
    派生口径（不加状态列）：failed 且同 file_path 无更新的 published 运行，
    重传成功自动消数；见 index_run_store_pg.list_pending_failures。
    """
    from backend.rag.indexing.index_run_store_pg import get_index_run_store

    store = get_index_run_store()
    items = await asyncio.to_thread(store.list_pending_failures, limit=limit)
    total = await asyncio.to_thread(store.count_pending_failures)
    return {"ok": True, "total": total, "items": items}


@router.get("/upload/{upload_id}/stream")
async def stream_upload_progress(upload_id: str, request: Request):
    """SSE 订阅：实时推送上传 + 索引进度（仅上传者本人/admin 可订阅）。

    事件类型：
      stage  → {stage: uploading|parsing|chunking|embedding|writing|done|error, message}
      done   → 包含 doc 信息
      error  → 索引失败

    通道路由（上传级，按 _celery_routed 标记）：
      已入队 Celery → Redis 轮询通道（Worker 在另一进程，进程内队列没有它的事件）；
      未入队 → 进程内队列模式。队列模式消费循环每轮复查标记，
      入队成功后无缝切换 Redis 轮询（携带 last_sig 去重）。
      入队失败回退本机索引时标记不会出现，全程队列模式。
    """
    if not _ensure_progress_owner(request, upload_id):
        # 统一「不存在或已过期」：不区分「无权」与「不存在」，不泄露存在性
        async def forbidden():
            yield _sse_encode("error", {"message": f"upload_id {upload_id} 不存在或已过期"})
        return StreamingResponse(forbidden(), media_type="text/event-stream")

    if upload_id in _celery_routed:
        return _redis_poll_stream_response(upload_id)

    queue = _progress_queues.get(upload_id)
    if queue is None:
        async def not_found():
            yield _sse_encode("error", {"message": f"upload_id {upload_id} 不存在或已过期"})
        return StreamingResponse(not_found(), media_type="text/event-stream")

    async def event_stream():
        last_sig: str | None = None
        try:
            while True:
                # Celery 入队成功（后台任务与 SSE 并发竞速）→ 切 Redis 轮询
                if upload_id in _celery_routed:
                    async for chunk in _redis_poll_events(upload_id, last_sig=last_sig):
                        yield chunk
                    return
                try:
                    evt = await asyncio.wait_for(
                        queue.get(), timeout=SSE_KEEPALIVE_TIMEOUT_SECONDS)
                except asyncio.TimeoutError:
                    # P2 心跳:大文件索引期间可能数分钟无事件,
                    # 无心跳的 SSE 长连接容易被代理/网关断开
                    yield ": keepalive\n\n"
                    continue
                if evt is None:
                    break
                # 关键：保留 stage 字段在 data 中 — 前端 onmessage 解析 payload.stage
                # _sse_encode 的 event 参数（stage）不再用作 SSE event name（无 event: 字段）
                import json as _json
                last_sig = _json.dumps(evt, sort_keys=True, ensure_ascii=False, default=str)
                yield _sse_encode("message", evt)
        finally:
            # SSE 断开 → 清理队列（防内存泄漏）
            _progress_queues.pop(upload_id, None)

    return StreamingResponse(event_stream(), media_type="text/event-stream")
