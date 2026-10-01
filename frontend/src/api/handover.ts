/** 边坡移交域请求封装：自动带当前班组会话凭据，并统一解读 401/403/409。 */
import { useAuthStore } from '@/stores/auth'

export interface ApiResponse<T> {
  status: number
  data: T
}

function authHeader(): Record<string, string> {
  const auth = useAuthStore()
  return auth.loggedIn ? { Authorization: `Bearer ${auth.token}` } : {}
}

/** 凭据轮换后若返回的是双方新会话，自动切到当前班组的新 token。 */
function absorbRotatedTokens(entry: any) {
  const rotated = (entry?.rotated_tokens
    ?? entry?.transfer?.rotated_tokens) as Record<string, string> | undefined
  if (!rotated) return
  const auth = useAuthStore()
  const fresh = rotated[auth.teamCode]
  if (fresh) auth.setSession(fresh, auth.teamCode, auth.teamName)
}

export async function handoverRequest<T = any>(
  method: string,
  path: string,
  body?: unknown,
): Promise<ApiResponse<T>> {
  const res = await fetch(`/api/slope-handover${path}`, {
    method,
    headers: { 'Content-Type': 'application/json', ...authHeader() },
    body: body === undefined ? undefined : JSON.stringify(body),
  }).catch((error: unknown) => {
    throw new Error(`接口请求失败：${error instanceof Error ? error.message : '请求未送达'}`)
  })
  const data = await res.json().catch(() => ({}))
  if (res.ok) absorbRotatedTokens(data?.entry)
  return { status: res.status, data }
}

/** 会话失效（401）时由调用方统一清理登录态。 */
export function isSessionExpired(status: number): boolean {
  return status === 401
}
