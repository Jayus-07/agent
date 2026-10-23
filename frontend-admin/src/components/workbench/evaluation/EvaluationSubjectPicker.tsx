'use client'

import { useState } from 'react'
import { listRbacUsers, type RbacUser } from '@/api/rbac'

export default function EvaluationSubjectPicker({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const [search, setSearch] = useState('')
  const [users, setUsers] = useState<RbacUser[]>([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const control = 'rounded-md border border-border-subtle bg-surface-base px-3 py-2 text-sm'
  return <fieldset className="space-y-2"><legend className="mb-1 text-sm">权限测试身份（可选）</legend><div className="flex gap-2"><input aria-label="查找测试用户" className={`${control} min-w-0 flex-1`} value={search} onChange={e => setSearch(e.target.value)} placeholder="搜索姓名或账号" /><button className={control} disabled={busy} onClick={() => void (async () => { setBusy(true); setError(''); try { setUsers((await listRbacUsers({ search, status: 1, pageSize: 20 })).items) } catch (e) { setError(e instanceof Error ? e.message : '用户读取失败') } finally { setBusy(false) } })()}>查找</button></div><select aria-label="权限测试身份" className={`${control} w-full`} value={value} onChange={e => onChange(e.target.value)}><option value="">不模拟身份，运行常规评测</option>{users.map(user => <option key={user.userId} value={user.userId}>{user.realName || user.username} · {user.dept || '未分配部门'} · {user.platformRole}</option>)}</select><p className="text-xs text-text-muted">显示本次查找的前 20 位启用用户；执行时按服务端当前角色和部门校验。</p>{error && <p role="alert" className="text-xs text-red-700">{error}</p>}</fieldset>
}
