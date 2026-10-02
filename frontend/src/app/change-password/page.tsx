"use client";

/**
 * /change-password — 临时密码首次登录强制改密页（TD-04，P6.3 闭环前端半边）。
 *
 * 后端已就绪（auth_local.py /auth/change-password + JWT must_change_password
 * 门禁），此前前端无消费方：用户被 403 横幅卡死在 /agent。本页是唯一出路：
 * 旧密码 + 新密码（≥8 位、不得与旧相同，后端二次校验）→ 换发全新正常 token
 * → 进入 /agent。
 */

import { FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { changePassword, getAccessToken } from "@/lib/auth";

export default function ChangePasswordPage() {
  const router = useRouter();
  const [oldPwd, setOldPwd] = useState("");
  const [newPwd, setNewPwd] = useState("");
  const [confirmPwd, setConfirmPwd] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  // 无 token 不可能完成改密（后端 401）——直接回登录页
  useEffect(() => {
    if (typeof window !== "undefined" && !getAccessToken()) {
      router.replace("/login?redirect=/change-password");
    }
  }, [router]);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (loading) return;
    setError("");
    if (!oldPwd || !newPwd || !confirmPwd) {
      setError("请填写全部密码字段");
      return;
    }
    if (newPwd.length < 8) {
      setError("新密码长度至少 8 位");
      return;
    }
    if (newPwd === oldPwd) {
      setError("新密码不能与旧密码相同");
      return;
    }
    if (newPwd !== confirmPwd) {
      setError("两次输入的新密码不一致");
      return;
    }
    setLoading(true);
    try {
      await changePassword(oldPwd, newPwd);
      // 新 token 已替换本地存储；旧会话已全部撤销，进入正常工作台
      router.replace("/agent");
    } catch (err) {
      setError(err instanceof Error ? err.message : "修改失败，请稍后重试");
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="min-h-screen flex items-center justify-center bg-surface-base px-4">
      <div className="w-full max-w-sm rounded-2xl border border-border-subtle bg-surface-base p-8 shadow-sm">
        <h1 className="text-lg font-semibold text-text-primary">修改登录密码</h1>
        <p className="mt-2 text-xs text-text-muted leading-relaxed">
          首次登录使用的是临时密码，为保障账号安全，请先设置新密码后再继续使用。
          修改后当前设备保持登录，其他设备将退出。
        </p>
        <form onSubmit={handleSubmit} className="mt-6 space-y-4">
          <div>
            <label htmlFor="old-password" className="block text-xs text-text-secondary mb-1.5">
              临时密码
            </label>
            <input
              id="old-password"
              type="password"
              autoComplete="current-password"
              value={oldPwd}
              onChange={(e) => setOldPwd(e.target.value)}
              className="w-full h-10 px-3 rounded-lg border border-border-subtle bg-surface-base text-sm text-text-primary outline-none focus:border-accent"
              placeholder="请输入临时密码"
            />
          </div>
          <div>
            <label htmlFor="new-password" className="block text-xs text-text-secondary mb-1.5">
              新密码（至少 8 位）
            </label>
            <input
              id="new-password"
              type="password"
              autoComplete="new-password"
              value={newPwd}
              onChange={(e) => setNewPwd(e.target.value)}
              className="w-full h-10 px-3 rounded-lg border border-border-subtle bg-surface-base text-sm text-text-primary outline-none focus:border-accent"
              placeholder="请输入新密码"
            />
          </div>
          <div>
            <label htmlFor="confirm-password" className="block text-xs text-text-secondary mb-1.5">
              确认新密码
            </label>
            <input
              id="confirm-password"
              type="password"
              autoComplete="new-password"
              value={confirmPwd}
              onChange={(e) => setConfirmPwd(e.target.value)}
              className="w-full h-10 px-3 rounded-lg border border-border-subtle bg-surface-base text-sm text-text-primary outline-none focus:border-accent"
              placeholder="请再次输入新密码"
            />
          </div>
          {error && (
            <p role="alert" className="text-xs text-red-500">
              {error}
            </p>
          )}
          <button
            type="submit"
            disabled={loading}
            className="w-full h-10 rounded-lg bg-accent text-white text-sm font-medium hover:bg-accent-hover transition-colors disabled:opacity-50"
          >
            {loading ? "提交中…" : "确认修改并继续"}
          </button>
        </form>
      </div>
    </main>
  );
}
