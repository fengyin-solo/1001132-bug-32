<template>
  <section class="page" data-module="slope-handover">
    <!-- 未登录：先领取班组会话 -->
    <div v-if="!auth.loggedIn" class="panel" style="max-width: 420px; margin: 40px auto">
      <h2>边坡移交工作台</h2>
      <p class="page-desc">所有检查与移交都以“班组会话 + 权限快照”鉴权。请先选择班组领取会话凭据。</p>
      <div class="inline-form" style="margin-top: 12px">
        <select v-model="loginCode">
          <option value="" disabled>选择责任班组</option>
          <option v-for="t in auth.teams" :key="t.code" :value="t.code">{{ t.name }}</option>
        </select>
        <input v-model="loginName" placeholder="值班检查人（可选）" />
        <button class="btn primary" type="button" :disabled="!loginCode" @click="doLogin">领取会话</button>
      </div>
      <p v-if="errorMessage" class="error-text">{{ errorMessage }}</p>
    </div>

    <template v-else>
      <header class="page-head">
        <div>
          <h2>边坡移交工作台 · {{ auth.teamName }}</h2>
          <p class="page-desc">
            生效时间以双方签署记录为准；旧班会话随移交事务联动失效。会话凭据：
            <code class="muted">{{ auth.token.slice(0, 16) }}…</code>
          </p>
        </div>
        <div class="page-actions">
          <button class="btn" type="button" @click="reloadAll">刷新</button>
          <button class="btn ghost" type="button" @click="doLogout">退出会话</button>
        </div>
      </header>

      <p v-if="notice" :class="notice.ok ? 'ok-text' : 'error-text'">{{ notice.text }}</p>

      <nav class="tab-bar">
        <button v-for="t in tabs" :key="t.key" class="tab" :class="{ active: tab === t.key }"
                type="button" @click="switchTab(t.key)">{{ t.label }}</button>
      </nav>

      <!-- 我的待办 -->
      <div v-if="tab === 'todo'" class="panel">
        <h3>本班组待办（工单归属随移交事务改派）</h3>
        <table class="data-table">
          <thead><tr><th>类型</th><th>事项</th><th>状态</th><th>时间</th></tr></thead>
          <tbody>
            <tr v-for="t in todos" :key="t.id">
              <td>{{ t.type }}</td><td>{{ t.title }}</td>
              <td><span class="badge" :class="t.status === '待办' ? 'draft' : 'owned'">{{ t.status }}</span></td>
              <td>{{ t.created_at }}</td>
            </tr>
            <tr v-if="!todos.length"><td colspan="4" class="empty-state">暂无待办</td></tr>
          </tbody>
        </table>
      </div>

      <!-- 整改工单 -->
      <div v-if="tab === 'order'" class="panel">
        <h3>整改工单（仅当前权限快照归属本班组的工单可见）</h3>
        <table class="data-table">
          <thead><tr><th>工单编号</th><th>边坡</th><th>事项</th><th>归属</th><th>状态</th><th>操作</th></tr></thead>
          <tbody>
            <tr v-for="o in orders" :key="o.id">
              <td>{{ o.order_no }}</td><td>{{ o['边坡编号'] }}</td><td>{{ o.title }}</td>
              <td>{{ o.owner_name }}</td>
              <td><span class="badge" :class="o.status === '待整改' ? 'draft' : 'owned'">{{ o.status }}</span></td>
              <td><button v-if="o.status === '待整改'" class="link" type="button" @click="closeOrder(o.id)">办结</button></td>
            </tr>
            <tr v-if="!orders.length"><td colspan="6" class="empty-state">暂无整改工单</td></tr>
          </tbody>
        </table>
      </div>

      <!-- 边坡台账：提交检查 / 查看快照与历史 -->
      <div v-if="tab === 'slope'" class="panel">
        <div class="inline-form">
          <label class="filter-item"><span>显示范围</span>
            <select v-model="slopeScope">
              <option value="all">全部边坡</option>
              <option value="mine">仅本班组（当前快照）</option>
              <option value="pool">仅待认领池</option>
            </select>
          </label>
          <button class="btn" type="button" @click="loadSlopes">查询</button>
        </div>
        <table class="data-table">
          <thead><tr>
            <th>边坡编号</th><th>所属路段</th><th>状态</th><th>责任班组</th>
            <th>归属</th><th>快照</th><th>待整改</th><th>操作</th>
          </tr></thead>
          <tbody>
            <tr v-for="s in slopes" :key="s.id">
              <td>{{ s['边坡编号'] }}</td><td>{{ s['所属路段'] }}</td><td>{{ s.status }}</td>
              <td>{{ s['责任班组'] }}</td>
              <td><span class="badge" :class="s['归属状态'] === '待认领' ? 'pool' : 'owned'">{{ s['归属状态'] }}</span></td>
              <td>v{{ s['当前快照版本'] ?? '—' }}</td><td>{{ s['待整改工单数'] }}</td>
              <td class="row-actions">
                <button class="link" type="button" @click="openInspect(s)">提交检查</button>
                <button class="link" type="button" @click="openDetail(s.id)">快照/历史</button>
              </td>
            </tr>
            <tr v-if="!slopes.length"><td colspan="8" class="empty-state">暂无数据</td></tr>
          </tbody>
        </table>
      </div>

      <!-- 移交会签 -->
      <div v-if="tab === 'transfer'" class="panel">
        <h3>发起移交（仅当前责任班组可发起，缺接收方即迁入待认领池）</h3>
        <div class="inline-form">
          <select v-model="transferForm.toCode">
            <option :value="null">迁入待认领池</option>
            <option v-for="t in auth.teams.filter(x => x.code !== auth.teamCode)" :key="t.code" :value="t.code">
              移交给 {{ t.name }}
            </option>
          </select>
          <input v-model="transferForm.remark" placeholder="移交备注（可选）" style="width: 220px" />
          <span class="muted">已选边坡：{{ transferForm.slopeIds.length }} 处（在下表勾选本班组边坡）</span>
          <button class="btn primary" type="button" :disabled="!transferForm.slopeIds.length"
                  @click="createTransfer">发起会签</button>
        </div>
        <table class="data-table">
          <thead><tr>
            <th>选择</th><th>边坡编号</th><th>责任班组</th><th>快照</th><th>状态</th>
          </tr></thead>
          <tbody>
            <tr v-for="s in mineSlopesForTransfer" :key="s.id">
              <td><input type="checkbox" :value="s.id" v-model="transferForm.slopeIds" /></td>
              <td>{{ s['边坡编号'] }}</td><td>{{ s['责任班组'] }}</td>
              <td>v{{ s['当前快照版本'] }}</td><td>{{ s.status }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div v-if="tab === 'transfer'" class="panel">
        <h3>会签记录（双方签署齐备当刻生效，并发只接受一个版本）</h3>
        <table class="data-table">
          <thead><tr>
            <th>移交单</th><th>边坡</th><th>移交方</th><th>接收方</th><th>状态</th>
            <th>签署记录</th><th>生效时间</th><th>操作</th>
          </tr></thead>
          <tbody>
            <tr v-for="t in transfers" :key="t.id">
              <td>{{ t.transfer_no }}</td>
              <td>{{ t.slope_ids.join('、') }}</td>
              <td>{{ t.from_name }}</td><td>{{ t.to_name }}</td>
              <td><span class="badge" :class="statusClass(t.status)">{{ t.status }}</span></td>
              <td>
                <span v-for="g in t.signatures" :key="g.team_code" class="muted">
                  {{ g.team_name }}·{{ g.signer }} {{ g.agreed ? '同意' : '拒签' }} {{ g.signed_at.slice(5, 16) }}；
                </span>
              </td>
              <td>{{ t.effective_at ?? '—' }}</td>
              <td class="row-actions">
                <template v-if="t.status === '待会签' && canSign(t)">
                  <button class="link" type="button" @click="sign(t, true)">同意签署</button>
                  <button class="link" type="button" @click="sign(t, false)">拒签</button>
                </template>
              </td>
            </tr>
            <tr v-if="!transfers.length"><td colspan="8" class="empty-state">暂无移交记录</td></tr>
          </tbody>
        </table>
      </div>

      <!-- 待认领池 -->
      <div v-if="tab === 'pool'" class="panel">
        <h3>待认领池：无归属/迁移缺归属边坡先落这里，显式认领后才签发权限快照</h3>
        <table class="data-table">
          <thead><tr><th>边坡编号</th><th>所属路段</th><th>类型</th><th>状态</th><th>操作</th></tr></thead>
          <tbody>
            <tr v-for="s in pool" :key="s.id">
              <td>{{ s['边坡编号'] }}</td><td>{{ s['所属路段'] }}</td>
              <td>{{ s['边坡类型'] }}</td><td>{{ s.status }}</td>
              <td><button class="link" type="button" @click="claim(s.id)">本班组认领</button></td>
            </tr>
            <tr v-if="!pool.length"><td colspan="5" class="empty-state">待认领池为空</td></tr>
          </tbody>
        </table>
      </div>
    </template>

    <!-- 提交检查弹窗 -->
    <div v-if="inspectTarget" class="dialog-mask" @click.self="inspectTarget = null">
      <div class="dialog">
        <h3>提交检查 · {{ inspectTarget['边坡编号'] }}（{{ inspectTarget['责任班组'] }}）</h3>
        <p class="muted">仅当前权限快照班组可提交；若本边坡已移交，上一班旧会话会被拒绝。</p>
        <textarea v-model="inspectForm.content" rows="3" style="width: 100%"
                  placeholder="检查情况，如：坡面新增裂缝、排水沟通畅…"></textarea>
        <label class="inline-form" style="margin-top: 8px">
          <input type="checkbox" v-model="inspectForm.abnormal" /> 发现异常（自动生成整改工单并进本班组待办）
        </label>
        <p v-if="inspectError" class="error-text">{{ inspectError }}</p>
        <div class="page-actions" style="text-align: right; margin-top: 10px">
          <button class="btn" type="button" @click="inspectTarget = null">取消</button>
          <button class="btn primary" type="button" @click="submitInspection">提交</button>
        </div>
      </div>
    </div>

    <!-- 快照与历史详情弹窗 -->
    <div v-if="detail" class="dialog-mask" @click.self="detail = null">
      <div class="dialog">
        <h3>{{ detail.slope['边坡编号'] }} · 权限快照与历史归属</h3>
        <dl class="kv">
          <dt>当前责任班组</dt><dd>{{ detail.slope['责任班组'] }}（快照 v{{ detail.slope['当前快照版本'] ?? '—' }}）</dd>
          <dt>待整改工单</dt><dd>{{ detail.slope['待整改工单数'] }}</dd>
        </dl>
        <h3 style="margin-top: 12px">快照版本链</h3>
        <table class="data-table">
          <thead><tr><th>版本</th><th>班组</th><th>原因</th><th>签发时间</th><th>当前</th></tr></thead>
          <tbody>
            <tr v-for="s in detail.snapshots" :key="s.id">
              <td>v{{ s.version }}</td><td>{{ s.team_name ?? '待认领池' }}</td>
              <td>{{ s.reason }}</td><td>{{ s.issued_at }}</td>
              <td>{{ s.active ? '✔' : '' }}</td>
            </tr>
          </tbody>
        </table>
        <h3 style="margin-top: 12px">历史巡检（归属按提交时快照保留）</h3>
        <table class="data-table">
          <thead><tr><th>编号</th><th>归属班组</th><th>快照</th><th>检查人</th><th>内容</th><th>时间</th></tr></thead>
          <tbody>
            <tr v-for="i in detail.inspections" :key="i.id">
              <td>{{ i.inspection_no }}</td><td>{{ i.team_name }}</td><td>v{{ i.snapshot_version }}</td>
              <td>{{ i.inspector }}</td><td>{{ i.content }}{{ i.abnormal ? ' ⚠' : '' }}</td><td>{{ i.created_at }}</td>
            </tr>
            <tr v-if="!detail.inspections.length"><td colspan="6" class="empty-state">暂无巡检</td></tr>
          </tbody>
        </table>
        <div class="page-actions" style="text-align: right; margin-top: 10px">
          <button class="btn" type="button" @click="detail = null">关闭</button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'

import { handoverRequest, isSessionExpired } from '@/api/handover'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const tabs = [
  { key: 'todo', label: '我的待办' },
  { key: 'order', label: '整改工单' },
  { key: 'slope', label: '边坡台账' },
  { key: 'transfer', label: '移交会签' },
  { key: 'pool', label: '待认领池' },
]
const tab = ref('todo')
const notice = ref<{ ok: boolean; text: string } | null>(null)
const errorMessage = ref('')
const loginCode = ref('')
const loginName = ref('')

const todos = ref<any[]>([])
const orders = ref<any[]>([])
const slopes = ref<any[]>([])
const transfers = ref<any[]>([])
const pool = ref<any[]>([])
const slopeScope = ref('all')

const transferForm = reactive<{ slopeIds: number[]; toCode: string | null; remark: string }>({
  slopeIds: [], toCode: null, remark: '',
})
const mineSlopesForTransfer = ref<any[]>([])

const inspectTarget = ref<any | null>(null)
const inspectForm = reactive({ content: '', abnormal: true })
const inspectError = ref('')
const detail = ref<any | null>(null)

function flash(ok: boolean, text: string) {
  notice.value = { ok, text }
  window.setTimeout(() => (notice.value = null), 4000)
}

async function handle<T>(p: Promise<{ status: number; data: T }>): Promise<T | null> {
  const { status, data } = await p
  const anyData = data as any
  if (isSessionExpired(status)) {
    auth.logout()
    flash(false, anyData?.detail ?? '会话已随移交失效，请用当前班组重新登录')
    return null
  }
  if (status >= 400) {
    flash(false, anyData?.detail ?? anyData?.message ?? `请求失败（${status}）`)
    return null
  }
  return data
}

async function doLogin() {
  errorMessage.value = ''
  try {
    await auth.login(loginCode.value, loginName.value || undefined)
    await reloadAll()
  } catch (e) {
    errorMessage.value = e instanceof Error ? e.message : '登录失败'
  }
}
function doLogout() {
  auth.logout()
}

async function loadTodos() {
  const r = await handle(handoverRequest('GET', '/todos'))
  if (r) todos.value = (r as any).items
}
async function loadOrders() {
  const r = await handle(handoverRequest('GET', '/work-orders'))
  if (r) orders.value = (r as any).items
}
async function loadInspectionsCount() {
  // 占位：详情里直接拉取
}
async function loadSlopes() {
  const params =
    slopeScope.value === 'mine' ? `?team_code=${auth.teamCode}`
    : slopeScope.value === 'pool' ? '?pending_claim=true' : ''
  const r = await handle(handoverRequest('GET', `/slopes${params}`))
  if (r) slopes.value = (r as any).items
}
async function loadMineForTransfer() {
  const r = await handle(handoverRequest('GET', `/slopes?team_code=${auth.teamCode}`))
  if (r) mineSlopesForTransfer.value = (r as any).items
}
async function loadTransfers() {
  const r = await handle(handoverRequest('GET', '/transfers'))
  if (r) transfers.value = (r as any).items
}
async function loadPool() {
  const r = await handle(handoverRequest('GET', '/claim-pool'))
  if (r) pool.value = (r as any).items
}

async function reloadAll() {
  if (!auth.loggedIn) return
  await Promise.all([loadTodos(), loadOrders(), loadSlopes(), loadTransfers(), loadPool(), loadMineForTransfer()])
}
function switchTab(key: string) {
  tab.value = key
  void reloadAll()
}

function statusClass(status: string): string {
  if (status === '已生效') return 'signed'
  if (status === '待会签') return 'draft'
  if (status === '已作废') return 'void'
  return 'rejected'
}
function canSign(t: any): boolean {
  const parties = new Set([t.from_code, t.to_code].filter(Boolean))
  const signed = new Set(t.signatures.map((g: any) => g.team_code))
  return parties.has(auth.teamCode) && !signed.has(auth.teamCode)
}

function openInspect(s: any) {
  inspectTarget.value = s
  inspectForm.content = ''
  inspectForm.abnormal = true
  inspectError.value = ''
}
async function submitInspection() {
  inspectError.value = ''
  const id = inspectTarget.value.id
  const r = await handle(handoverRequest('POST', `/slopes/${id}/inspections`, {
    content: inspectForm.content, abnormal: inspectForm.abnormal,
  }))
  if (!r) {
    inspectError.value = notice.value?.text ?? '提交被拒绝'
    return
  }
  inspectTarget.value = null
  flash(true, (r as any).message ?? '检查已提交')
  await reloadAll()
}

async function openDetail(id: number) {
  const r = await handle(handoverRequest('GET', `/slopes/${id}`))
  if (r) detail.value = r
}

async function createTransfer() {
  const r = await handle(handoverRequest('POST', '/transfers', {
    slope_ids: transferForm.slopeIds, to_code: transferForm.toCode, remark: transferForm.remark,
  }))
  if (!r) return
  transferForm.slopeIds = []
  transferForm.remark = ''
  flash(true, (r as any).message ?? '移交单已发起')
  await reloadAll()
}
async function sign(t: any, agree: boolean) {
  const r = await handle(handoverRequest('POST', `/transfers/${t.id}/sign`, { agree }))
  if (!r) {
    await reloadAll()
    return
  }
  flash(true, (r as any).message ?? '已签署')
  await reloadAll()
}
async function claim(id: number) {
  const r = await handle(handoverRequest('POST', '/claims', { id }))
  if (!r) return
  flash(true, (r as any).message ?? '认领成功，新会话已签发')
  await reloadAll()
}
async function closeOrder(id: number) {
  const r = await handle(handoverRequest('POST', `/work-orders/${id}/close`))
  if (r) {
    flash(true, (r as any).message ?? '工单已办结')
    await reloadAll()
  }
}

onMounted(async () => {
  await auth.loadTeams()
  if (auth.loggedIn) await reloadAll()
})
</script>
