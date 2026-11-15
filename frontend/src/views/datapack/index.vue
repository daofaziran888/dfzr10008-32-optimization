<template>
  <section class="page" data-module="datapack">
    <header class="page-head">
      <div>
        <h2>数据模型封装管理</h2>
        <p class="page-desc">维护数据模型封装的字段映射、聚合规则与缓存策略，为数据模型封装提供可验证的数据工程流程。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记模型封装</button>
        <button class="btn" type="button" @click="exportRows">导出数据模型封装清单</button>
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
            <button class="link" type="button" @click="showXrefSnapshot(row)">关联快照</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无数据模型封装数据，可先登记模型封装</td>
        </tr>
      </tbody>
    </table>

    <p v-if="snapshotMessage" class="snapshot-message">{{ snapshotMessage }}</p>

    <footer class="page-foot">
      <span>共 {{ total }} 条数据模型封装记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>

const ENDPOINT = '/api/datapack'
const columns = ["封装编号", "封装名称", "原始数据源", "输出结构", "字段映射", "聚合规则", "缓存策略", "封装状态", "快照版本"]
const actions = ["发布封装", "废弃封装", "删除封装"]
const statuses = ["草稿", "已封装", "已发布", "已废弃"]
const stats = [{"label": "模型封装总数", "value": 0}, {"label": "已发布封装", "value": 0}, {"label": "已废弃封装", "value": 0}]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const snapshotMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '模型封装登记入口尚未接入审批流'
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    if (!response.ok) {
      throw new Error('数据模型封装动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '数据模型封装操作失败'
  }
}

async function showXrefSnapshot(row: Row) {
  errorMessage.value = ''
  snapshotMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/xref-snapshot`)
    const payload = await response.json()
    if (!response.ok) {
      throw new Error(payload.detail ?? '读取关联快照失败')
    }
    snapshotMessage.value = `封装 ${row.封装编号} 读取关联规则 ${payload.规则编号} 的最新完整快照：v${payload.版本}（水位 ${payload.水位 || '—'}，字段 ${payload.字段?.length ?? 0} 个，记录 ${payload.记录数 ?? 0} 条）`
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '读取关联快照失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('模型封装列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '数据模型封装列表读取失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.snapshot-message {
  font-size: 13px;
  color: var(--brand);
  margin: 8px 0 0;
}
</style>
