<template>
  <section class="page" data-module="crossref">
    <header class="page-head">
      <div>
        <h2>跨源数据关联管理</h2>
        <p class="page-desc">维护跨源数据关联的字段映射、关联类型与时效配置，为跨源数据关联提供可验证的数据工程流程。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记关联规则</button>
        <button class="btn" type="button" @click="exportRows">导出跨源数据关联清单</button>
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
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] || '—' }}</td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
            <button class="link" type="button" @click="openDetail(row)">刷新详情</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无跨源数据关联数据，可先登记关联规则</td>
        </tr>
      </tbody>
    </table>

    <section v-if="detail" class="detail-panel">
      <header class="detail-head">
        <h3>规则 {{ detail.规则编号 }} 的增量刷新</h3>
        <button class="link" type="button" @click="closeDetail">收起</button>
      </header>

      <form class="config-form" @submit.prevent="saveConfig">
        <label class="filter-item">
          <span>主数据源</span>
          <select v-model="configForm.主数据源">
            <option v-for="name in primaryOptions" :key="name" :value="name">{{ name }}</option>
          </select>
        </label>
        <label class="filter-item">
          <span>关联数据源</span>
          <select v-model="configForm.关联数据源">
            <option v-for="name in linkedOptions" :key="name" :value="name">{{ name }}</option>
          </select>
        </label>
        <label class="filter-item">
          <span>主数据源关联字段</span>
          <input v-model="configForm.主字段" placeholder="如 store_id" />
        </label>
        <label class="filter-item">
          <span>关联数据源字段</span>
          <input v-model="configForm.关联字段" placeholder="如 store_id" />
        </label>
        <label class="filter-item">
          <span>关联时效</span>
          <select v-model="configForm.时效控制">
            <option v-for="item in timelinessOptions" :key="item" :value="item">{{ item }}</option>
          </select>
        </label>
        <button class="btn primary" type="submit">保存配置</button>
      </form>

      <div class="refresh-bar">
        <span>当前水位：{{ detail.水位 || '—' }}</span>
        <span>刷新状态：{{ refreshStateText }}</span>
        <span>结果总数：{{ results.length }}</span>
        <button class="btn" type="button" @click="runRefresh(false)">增量刷新</button>
        <button class="btn ghost" type="button" @click="runRefresh(true)">模拟中断刷新</button>
        <button class="btn ghost" type="button" @click="verifyResults">校验一致性</button>
      </div>
      <p v-if="detailMessage" class="detail-message">{{ detailMessage }}</p>

      <h4 class="detail-subhead">跨源关联结果（同一主键只保留一份）</h4>
      <table class="data-table">
        <thead>
          <tr>
            <th v-for="column in resultColumns" :key="column">{{ column }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(row, index) in results" :key="index">
            <td v-for="column in resultColumns" :key="column">{{ row[column] ?? '—' }}</td>
          </tr>
          <tr v-if="!results.length">
            <td :colspan="Math.max(resultColumns.length, 1)" class="empty-state">暂无关联结果，请先执行增量刷新</td>
          </tr>
        </tbody>
      </table>

      <h4 class="detail-subhead">字段匹配快照（模型封装读取最新完整版本，旧版本保留以便追溯）</h4>
      <table class="data-table">
        <thead>
          <tr>
            <th>版本</th>
            <th>是否完整</th>
            <th>水位</th>
            <th>生成时间</th>
            <th>完成时间</th>
            <th>记录数</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="snap in snapshots" :key="String(snap.版本)">
            <td>v{{ snap.版本 }}</td>
            <td>{{ snap.完整 ? '完整' : '未完成' }}</td>
            <td>{{ snap.水位 || '—' }}</td>
            <td>{{ snap.生成时间 || '—' }}</td>
            <td>{{ snap.完成时间 || '—' }}</td>
            <td>{{ snap.记录数 }}</td>
          </tr>
          <tr v-if="!snapshots.length">
            <td colspan="6" class="empty-state">暂无快照，完成一次增量刷新后生成</td>
          </tr>
        </tbody>
      </table>
    </section>

    <footer class="page-foot">
      <span>共 {{ total }} 条跨源数据关联记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>
type Detail = Record<string, any>

const ENDPOINT = '/api/crossref'
const columns = ["规则编号", "主数据源", "关联数据源", "关联字段", "关联类型", "结果字段", "时效控制", "规则状态", "水位"]
const actions = ["启用关联", "暂停关联", "关闭关联"]
const statuses = ["草稿", "已启用", "已暂停", "已关闭"]
const stats = [{"label": "关联规则总数", "value": 0}, {"label": "已启用关联", "value": 0}, {"label": "已暂停关联", "value": 0}]
const timelinessOptions = ["实时", "小时级", "日级", "周级"]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)

const detail = ref<Detail | null>(null)
const configForm = ref({ 主数据源: '', 关联数据源: '', 主字段: '', 关联字段: '', 时效控制: '' })
const sourceNames = ref<string[]>([])
const results = ref<Detail[]>([])
const snapshots = ref<Detail[]>([])
const detailMessage = ref('')

const primaryOptions = computed(() => {
  const options = new Set(sourceNames.value)
  if (configForm.value.主数据源) options.add(configForm.value.主数据源)
  return [...options]
})

const linkedOptions = computed(() => {
  const options = new Set(sourceNames.value)
  if (configForm.value.关联数据源) options.add(configForm.value.关联数据源)
  return [...options]
})

const refreshStateText = computed(() => {
  const state = detail.value?.刷新状态
  if (!state || typeof state !== 'object') return '—'
  const parts = [state.状态 || '—']
  if (state.候选总数) parts.push(`${state.已处理}/${state.候选总数}`)
  if (state.最近刷新) parts.push(state.最近刷新)
  return parts.join('，')
})

const resultColumns = computed(() => {
  if (!results.value.length) return [] as string[]
  const keys = Object.keys(results.value[0]).filter((key) => key !== '快照版本')
  keys.push('快照版本')
  return keys
})

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '关联规则登记入口尚未接入审批流'
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    if (!response.ok) {
      throw new Error('跨源数据关联动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '跨源数据关联操作失败'
  }
}

async function openDetail(row: Row) {
  errorMessage.value = ''
  detailMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}`)
    if (!response.ok) {
      throw new Error('关联规则明细读取失败')
    }
    detail.value = await response.json()
    const mapping = (detail.value?.关联字段映射 ?? {}) as Record<string, string>
    const [主字段 = '', 关联字段 = ''] = Object.entries(mapping)[0] ?? []
    configForm.value = {
      主数据源: detail.value?.主数据源 ?? '',
      关联数据源: detail.value?.关联数据源 ?? '',
      主字段,
      关联字段,
      时效控制: detail.value?.时效控制 || '日级',
    }
    await Promise.all([loadResults(), loadSnapshots()])
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '关联规则明细读取失败'
  }
}

function closeDetail() {
  detail.value = null
  results.value = []
  snapshots.value = []
  detailMessage.value = ''
}

async function saveConfig() {
  if (!detail.value) return
  detailMessage.value = ''
  const mapping: Record<string, string> = {}
  if (configForm.value.主字段 && configForm.value.关联字段) {
    mapping[configForm.value.主字段] = configForm.value.关联字段
  }
  try {
    const response = await request(`${ENDPOINT}/${detail.value.id}/config`, {
      method: 'PUT',
      body: JSON.stringify({
        values: {
          主数据源: configForm.value.主数据源,
          关联数据源: configForm.value.关联数据源,
          时效控制: configForm.value.时效控制,
          关联字段映射: mapping,
        },
      }),
    })
    const payload = await response.json()
    detailMessage.value = payload.message ?? '配置已保存'
    if (payload.entry) {
      detail.value = payload.entry
    }
    await Promise.all([loadResults(), loadSnapshots(), reload()])
  } catch (error) {
    detailMessage.value = error instanceof Error ? error.message : '配置保存失败'
  }
}

async function runRefresh(simulateInterrupt: boolean) {
  if (!detail.value) return
  detailMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${detail.value.id}/refresh`, {
      method: 'POST',
      body: JSON.stringify({ values: simulateInterrupt ? { interrupt_after: 2 } : {} }),
    })
    const payload = await response.json()
    detailMessage.value = payload.message ?? '刷新已执行'
    await refreshDetail()
  } catch (error) {
    detailMessage.value = error instanceof Error ? error.message : '增量刷新失败'
  }
}

async function verifyResults() {
  if (!detail.value) return
  detailMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${detail.value.id}/verify`)
    const payload = await response.json()
    if (!response.ok) {
      throw new Error(payload.detail ?? '一致性校验失败')
    }
    detailMessage.value = `校验结果：${payload.说明}（快照 v${payload.快照版本}，结果 ${payload.结果总数} 条）`
  } catch (error) {
    detailMessage.value = error instanceof Error ? error.message : '一致性校验失败'
  }
}

async function refreshDetail() {
  if (!detail.value) return
  const response = await request(`${ENDPOINT}/${detail.value.id}`)
  if (response.ok) {
    detail.value = await response.json()
  }
  await Promise.all([loadResults(), loadSnapshots(), reload()])
}

async function loadResults() {
  if (!detail.value) return
  const response = await request(`${ENDPOINT}/${detail.value.id}/results`)
  if (response.ok) {
    const payload = await response.json()
    results.value = payload.items ?? []
  }
}

async function loadSnapshots() {
  if (!detail.value) return
  const response = await request(`${ENDPOINT}/${detail.value.id}/snapshots`)
  if (response.ok) {
    const payload = await response.json()
    snapshots.value = payload.items ?? []
  }
}

async function loadSources() {
  try {
    const response = await request(`${ENDPOINT}/sources`)
    if (response.ok) {
      const payload = await response.json()
      sourceNames.value = payload.sources ?? []
    }
  } catch {
    sourceNames.value = []
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('关联规则列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '跨源数据关联列表读取失败'
  }
}

onMounted(() => {
  void reload()
  void loadSources()
})
</script>

<style scoped>
.detail-panel {
  margin-top: 16px;
  padding: 12px;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
}
.detail-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.detail-head h3 {
  margin: 0;
  font-size: 15px;
}
.config-form {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: flex-end;
  margin: 12px 0;
}
.refresh-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  font-size: 13px;
  color: var(--muted);
  margin-bottom: 8px;
}
.detail-message {
  font-size: 13px;
  color: var(--brand);
  margin: 4px 0 8px;
}
.detail-subhead {
  font-size: 13px;
  color: var(--muted);
  margin: 12px 0 6px;
}
.detail-panel .data-table {
  margin-bottom: 8px;
}
</style>
