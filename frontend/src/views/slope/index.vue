<template>
  <section class="page" data-module="slope">
    <header class="page-head">
      <div>
        <h2>边坡防护管理</h2>
        <p class="page-desc">
          责任班组、归属状态以当前生效的权限快照为准，列表与详情同一口径；
          提交检查请到
          <RouterLink to="/slope_handover" class="link">边坡移交工作台</RouterLink>
          领取班组会话。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" @click="exportRows">导出边坡防护清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <label class="filter-item">
        <span>归属状态</span>
        <select v-model="claimFilter">
          <option value="">全部</option>
          <option value="owned">已认领</option>
          <option value="pool">待认领池</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>责任班组</th>
          <th>归属</th>
          <th>快照</th>
          <th>待整改</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td>{{ row['责任班组'] ?? '—' }}</td>
          <td>
            <span class="badge" :class="row['归属状态'] === '待认领' ? 'pool' : 'owned'">
              {{ row['归属状态'] ?? '—' }}
            </span>
          </td>
          <td>v{{ row['当前快照版本'] ?? '—' }}</td>
          <td>{{ row['待整改工单数'] ?? 0 }}</td>
          <td class="row-actions">
            <button class="link" type="button" @click="openDetail(Number(row.id))">快照/详情</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 5" class="empty-state">暂无边坡防护数据</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条边坡防护记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <div v-if="detail" class="dialog-mask" @click.self="detail = null">
      <div class="dialog">
        <h3>{{ detail.slope['边坡编号'] }} · 边坡防护详情</h3>
        <dl class="kv">
          <dt>所属路段</dt><dd>{{ detail.slope['所属路段'] }}</dd>
          <dt>边坡类型/坡高</dt><dd>{{ detail.slope['边坡类型'] }} / {{ detail.slope['坡高'] }}</dd>
          <dt>防护形式</dt><dd>{{ detail.slope['防护形式'] }}</dd>
          <dt>稳定性评级</dt><dd>{{ detail.slope['稳定性评级'] }}</dd>
          <dt>边坡状态</dt><dd>{{ detail.slope.status }}</dd>
          <dt>责任班组</dt><dd>{{ detail.slope['责任班组'] }}（快照 v{{ detail.slope['当前快照版本'] ?? '—' }}）</dd>
          <dt>待整改工单</dt><dd>{{ detail.slope['待整改工单数'] }}</dd>
        </dl>
        <h3 style="margin-top: 12px">权限快照链（归属唯一事实来源）</h3>
        <table class="data-table">
          <thead><tr><th>版本</th><th>班组</th><th>原因</th><th>签发时间</th><th>当前</th></tr></thead>
          <tbody>
            <tr v-for="s in detail.snapshots" :key="s.id">
              <td>v{{ s.version }}</td><td>{{ s.team_name ?? '待认领池' }}</td>
              <td>{{ s.reason }}</td><td>{{ s.issued_at }}</td>
              <td>{{ s.active ? '✔' : '' }}</td>
            </tr>
            <tr v-if="!detail.snapshots.length"><td colspan="5" class="empty-state">尚未签发快照（待认领池）</td></tr>
          </tbody>
        </table>
        <h3 style="margin-top: 12px">历史巡检（归属按提交时快照保留）</h3>
        <table class="data-table">
          <thead><tr><th>编号</th><th>归属班组</th><th>快照</th><th>内容</th><th>时间</th></tr></thead>
          <tbody>
            <tr v-for="i in detail.inspections" :key="i.id">
              <td>{{ i.inspection_no }}</td><td>{{ i.team_name }}</td><td>v{{ i.snapshot_version }}</td>
              <td>{{ i.content }}{{ i.abnormal ? ' ⚠' : '' }}</td><td>{{ i.created_at }}</td>
            </tr>
            <tr v-if="!detail.inspections.length"><td colspan="5" class="empty-state">暂无巡检</td></tr>
          </tbody>
        </table>
        <h3 style="margin-top: 12px">整改工单</h3>
        <table class="data-table">
          <thead><tr><th>工单</th><th>归属</th><th>状态</th><th>创建</th></tr></thead>
          <tbody>
            <tr v-for="o in detail.work_orders" :key="o.id">
              <td>{{ o.order_no }}</td><td>{{ o.owner_name }}</td><td>{{ o.status }}</td><td>{{ o.created_at }}</td>
            </tr>
            <tr v-if="!detail.work_orders.length"><td colspan="4" class="empty-state">暂无工单</td></tr>
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
import { onMounted, ref } from 'vue'

import { handoverRequest } from '@/api/handover'
import { request } from '@/api/client'

type Row = Record<string, string | number | null>

const ENDPOINT = '/api/slope'
const columns = ["边坡编号", "所属路段", "边坡类型", "坡高", "防护形式", "稳定性评级", "最近巡检", "边坡状态"]
const stats = ref([
  { label: "在册边坡", value: 0 },
  { label: "待认领", value: 0 },
  { label: "待整改工单", value: 0 },
])

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const claimFilter = ref('')
const filterFields = columns.slice(0, 3)
const detail = ref<any | null>(null)

function resetFilters() {
  filters.value = {}
  claimFilter.value = ''
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

async function openDetail(id: number) {
  errorMessage.value = ''
  const { status, data } = await handoverRequest('GET', `/slopes/${id}`)
  if (status >= 400) {
    errorMessage.value = (data as any)?.detail ?? '边坡详情读取失败'
    return
  }
  detail.value = data
}

async function reload() {
  errorMessage.value = ''
  const params = new URLSearchParams()
  for (const [k, v] of Object.entries(filters.value)) {
    if (v) params.append(k, v)
  }
  if (claimFilter.value === 'pool') params.append('pending_claim', 'true')
  if (claimFilter.value === 'owned') params.append('page', '1') // owned 走前端过滤
  try {
    const response = await request(`${ENDPOINT}?${params.toString()}`)
    if (!response.ok) throw new Error('边坡列表读取失败')
    const payload = await response.json()
    let items: any[] = payload.items ?? []
    if (claimFilter.value === 'owned') {
      items = items.filter((r) => r['归属状态'] === '已认领')
    }
    rows.value = items
    total.value = claimFilter.value === 'owned' ? items.length : (payload.total ?? items.length)
    stats.value = [
      { label: '在册边坡', value: payload.items?.length ?? items.length },
      { label: '待认领', value: (payload.items ?? items).filter((r: any) => r['归属状态'] === '待认领').length },
      { label: '待整改工单', value: (payload.items ?? items).reduce((n: number, r: any) => n + (r['待整改工单数'] ?? 0), 0) },
    ]
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '边坡防护列表读取失败'
  }
}

onMounted(reload)
</script>
