/**
 * src/api/errors.ts — 错误码机制（P0-1b）
 *
 * ## 这个文件解决什么
 * 把「HTTP 状态 / 原始异常」翻译成**稳定、可展示、可判定**的错误描述（`ErrorDescriptor`），
 * 让页面不必各写一套 `if (res.status === 401) ...`，也避免把后端英文报错直接甩给用户。
 *
 * ## 边界（很重要，别误读）
 * - 后端**当前尚未定义** `RAG_XXXX` 这类真实业务码，因此 `DOMAIN_ERRORS` 登记表**刻意为空**。
 *   本文件落的是**机制**：登记表 + 降级链 + 扩展点。**不是**「真实码已接通」。
 * - 真实码映射是**前后端联合项**：后端定义后，只需在登记表加一行，**调用方零改动**。
 * - 本文件是**纯模块**：不发起网络请求、不依赖 React、也不 import 网络层，便于单测。
 *   （`TIMEOUT_REASON` 反向被 `client.ts` 引用，方向是 client → errors，无循环依赖。）
 *
 * ## 降级链（`describeApiError` 判定顺序）
 *   ① 已登记的域码 / 客户端码  ② 客户端自身异常（超时、取消、网络不可达）
 *   ③ 按 HTTP 状态码兜底        ④ 全兜底
 *   任何一步都不抛异常 —— 「取不到码」不应成为新的故障点。
 *
 * ## 怎么扩展
 * ```ts
 * import { registerDomainErrors } from "@/api/errors";
 * registerDomainErrors({ RAG_INDEX_TIMEOUT: { message: "索引超时…", kind: "timeout", retriable: true } });
 * ```
 * 也可直接改下方 `DOMAIN_ERRORS` 字面量（更推荐：集中、可审阅、diff 清晰）。
 */

// ── 类型 ──────────────────────────────────────────────────────

/** 粗粒度归类：页面据此决定「重试 / 去登录 / 提示改输入」等交互 */
export type ErrorKind =
  | "auth" // 未登录 / 登录过期
  | "permission" // 已登录但无权
  | "validation" // 入参不合法
  | "not_found"
  | "conflict" // 并发冲突，刷新后重试通常有效
  | "rate_limit"
  | "server" // 5xx
  | "network" // 连不上
  | "timeout"
  | "canceled" // 调用方主动取消，通常静默处理
  | "unknown";

export interface ErrorDescriptor {
  /** 面向用户的短文案（已中文化，可直接渲染；不要再拼技术细节） */
  message: string;
  kind: ErrorKind;
  /** 原样重试是否有意义。false → 重试无益，应引导用户改输入 / 重新登录 / 找管理员 */
  retriable: boolean;
}

// ── ① 域错误码登记表（扩展点：后端定义真实码后逐行登记） ────────

/**
 * 后端业务错误码 → 描述。
 *
 * **当前刻意为空**：后端还没有 `RAG_xxx` / `AUTH_xxx` 等真实码，先不臆造。
 * 登记一行即生效，无需改动任何调用方。
 */
export const DOMAIN_ERRORS = {
  // 示例（等后端定义真实码后再放开，勿凭空启用）：
  // RAG_INDEX_FAILED: { message: "文档索引失败，请稍后重试或联系管理员", kind: "server", retriable: true },
} satisfies Record<string, ErrorDescriptor>;

/**
 * 域错误码类型。
 *
 * `keyof typeof DOMAIN_ERRORS` 让**已登记**的码获得字面量补全；
 * `(string & {})` 保留「后端已加码、前端尚未登记」时的兼容性
 * （TS 惯用写法：既保留补全，又不把类型锁死成 never）。
 */
export type DomainErrorCode = keyof typeof DOMAIN_ERRORS | (string & {});

/**
 * 登记表的「可下标视图」。
 *
 * `satisfies` 是**有意**保留字面量类型的（登记处显式可见 + 已登记的键获得字面量补全），
 * 但字面量类型 `{}` 无法用 `string` 下标访问 —— 故内部查询与扩展点统一经此视图操作。
 */
const domainIndex = DOMAIN_ERRORS as Record<string, ErrorDescriptor>;

// ── ② 客户端自身错误码（前端产生，不来自后端） ─────────────────

export const CLIENT_ERROR_CODES = {
  TIMEOUT: "TIMEOUT",
  NETWORK: "NETWORK",
  CANCELED: "CANCELED",
} as const;

export type ClientErrorCode =
  (typeof CLIENT_ERROR_CODES)[keyof typeof CLIENT_ERROR_CODES];

/**
 * 超时原因文案。
 *
 * 由 `client.ts` 在 `AbortController.abort(reason)` 时使用，这里是**唯一**定义处 ——
 * 原先该字符串在 client.ts 内联、在测试里再写一遍，属于典型的三处分散魔法值。
 */
export const TIMEOUT_REASON = "Request timeout";

const CLIENT_ERRORS: Record<ClientErrorCode, ErrorDescriptor> = {
  [CLIENT_ERROR_CODES.TIMEOUT]: {
    message: "请求超时，请检查网络后重试",
    kind: "timeout",
    retriable: true,
  },
  [CLIENT_ERROR_CODES.NETWORK]: {
    message: "无法连接到服务器，请检查网络或稍后重试",
    kind: "network",
    retriable: true,
  },
  [CLIENT_ERROR_CODES.CANCELED]: {
    message: "请求已取消",
    kind: "canceled",
    retriable: false,
  },
};

// ── ③ HTTP 状态兜底 ───────────────────────────────────────────

export const FALLBACK_BY_STATUS: Record<number, ErrorDescriptor> = {
  400: { message: "请求参数有误，请检查后重试", kind: "validation", retriable: false },
  401: { message: "登录状态已过期，请重新登录", kind: "auth", retriable: false },
  403: { message: "没有权限执行该操作，请联系管理员", kind: "permission", retriable: false },
  404: { message: "请求的资源不存在或已被删除", kind: "not_found", retriable: false },
  409: { message: "数据已被其他人修改，请刷新后重试", kind: "conflict", retriable: true },
  413: { message: "文件过大，请压缩或拆分后重试", kind: "validation", retriable: false },
  422: { message: "提交内容不符合要求，请检查后重试", kind: "validation", retriable: false },
  429: { message: "操作过于频繁，请稍后再试", kind: "rate_limit", retriable: true },
  500: { message: "服务暂时不可用，请稍后重试", kind: "server", retriable: true },
  502: { message: "服务暂时不可用，请稍后重试", kind: "server", retriable: true },
  503: { message: "服务正在维护中，请稍后重试", kind: "server", retriable: true },
  504: { message: "服务响应超时，请稍后重试", kind: "server", retriable: true },
};

export const UNKNOWN_ERROR: ErrorDescriptor = {
  message: "操作失败，请稍后重试",
  kind: "unknown",
  retriable: false,
};

// ── 判定与提取工具（全部不抛错） ───────────────────────────────

/** 是否为已登记的域错误码 */
export function isDomainErrorCode(value: unknown): value is DomainErrorCode {
  return typeof value === "string" && Object.prototype.hasOwnProperty.call(DOMAIN_ERRORS, value);
}

/** 是否为客户端自身码（TIMEOUT / NETWORK / CANCELED） */
export function isClientErrorCode(value: unknown): value is ClientErrorCode {
  return typeof value === "string" && Object.prototype.hasOwnProperty.call(CLIENT_ERRORS, value);
}

/** 是否为超时（复用 client.ts 的终止原因，避免字符串重复定义） */
export function isTimeoutError(err: unknown): boolean {
  return err instanceof Error && err.message === TIMEOUT_REASON;
}

/** 调用方主动取消（AbortError）—— 与超时区分：前者通常静默处理 */
export function isCanceledError(err: unknown): boolean {
  return (
    !!err &&
    typeof err === "object" &&
    (err as { name?: string }).name === "AbortError" &&
    !isTimeoutError(err)
  );
}

function extractCode(err: unknown): string | undefined {
  if (!err || typeof err !== "object") return undefined;
  const c = (err as { code?: unknown }).code;
  return typeof c === "string" && c ? c : undefined;
}

function extractStatus(err: unknown): number | undefined {
  if (!err || typeof err !== "object") return undefined;
  const s = (err as { status?: unknown }).status;
  return typeof s === "number" ? s : undefined;
}

// ── 主入口 ────────────────────────────────────────────────────

export interface ResolvedError extends ErrorDescriptor {
  /** 命中的错误码（已登记的域码或客户端码） */
  code?: string;
  /** HTTP 状态码（如可得） */
  status?: number;
  /** 原始错误，供上报 / 日志使用；**不要**直接渲染给用户 */
  cause: unknown;
}

/**
 * 把任意异常翻译成可展示、可判定的错误描述。**永不抛错。**
 *
 * 判定顺序见文件头「降级链」。
 */
export function describeApiError(err: unknown): ResolvedError {
  const status = extractStatus(err);
  const code = extractCode(err);

  // ① 已登记的码优先（登记表刻意为空时直接落到后面）
  if (isDomainErrorCode(code)) {
    const hit = domainIndex[code];
    if (hit) return { ...hit, code, status, cause: err };
  }
  if (isClientErrorCode(code)) {
    return { ...CLIENT_ERRORS[code], code, status, cause: err };
  }

  // ② 客户端自身异常（顺序：超时 → 取消 → 网络不可达）
  if (isTimeoutError(err)) {
    return {
      ...CLIENT_ERRORS[CLIENT_ERROR_CODES.TIMEOUT],
      code: CLIENT_ERROR_CODES.TIMEOUT,
      status,
      cause: err,
    };
  }
  if (isCanceledError(err)) {
    return {
      ...CLIENT_ERRORS[CLIENT_ERROR_CODES.CANCELED],
      code: CLIENT_ERROR_CODES.CANCELED,
      status,
      cause: err,
    };
  }
  if (err instanceof TypeError) {
    // fetch 在网络不可达时抛 TypeError（如 "Failed to fetch"）
    return {
      ...CLIENT_ERRORS[CLIENT_ERROR_CODES.NETWORK],
      code: CLIENT_ERROR_CODES.NETWORK,
      status,
      cause: err,
    };
  }

  // ③ 按 HTTP 状态兜底
  if (status !== undefined) {
    const exact = FALLBACK_BY_STATUS[status];
    if (exact) return { ...exact, code, status, cause: err };
    // 未登记的具体状态码 → 按大类兜底，避免落到"未知"
    if (status >= 500) return { ...FALLBACK_BY_STATUS[500], code, status, cause: err };
    if (status >= 400) return { ...FALLBACK_BY_STATUS[400], code, status, cause: err };
  }

  // ④ 全兜底
  return { ...UNKNOWN_ERROR, code, status, cause: err };
}

/**
 * 便捷包装：只要用户可读文案。
 *
 * 适用 `catch` 分支一句话提示；需要按 `kind` 分支交互时请用 `describeApiError`。
 */
export function toUserMessage(err: unknown): string {
  return describeApiError(err).message;
}

/**
 * 扩展点：批量登记域错误码（同名覆盖）。
 *
 * 运行时对 `DOMAIN_ERRORS` 的写入需要断言 —— 它被 `satisfies` 冻结为字面量类型 `{}`，
 * 但这是**有意的**：登记处应保持「显式可见」，不建议在业务代码里动态塞码。
 * 该函数主要用于**测试**与「后端正交模块自带错误码」的场景。
 *
 * @returns 本次实际新增/覆盖的键数
 */
export function registerDomainErrors(map: Record<string, ErrorDescriptor>): number {
  const table = domainIndex;
  let n = 0;
  for (const key of Object.keys(map)) {
    table[key] = map[key];
    n += 1;
  }
  return n;
}
