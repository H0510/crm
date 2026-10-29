<template>
  <div class="candidate-table">
    <div class="candidate-table__toolbar">
      <input v-model="keyword" class="input" placeholder="按昵称、微信号、手机号、备注筛选" />
      <span class="candidate-table__count">共 {{ filtered.length }} 条</span>
      <a class="btn btn--ghost btn--sm" :href="exportUrl" target="_blank" rel="noopener">导出 CSV</a>
    </div>

    <table>
      <thead>
        <tr>
          <th>昵称</th>
          <th>微信号</th>
          <th>手机号</th>
          <th>地区</th>
          <th>备注</th>
          <th>置信度</th>
          <th>采集时间</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="row in paged"
          :key="row.id"
          :class="{ 'is-active': selectedId === row.id }"
          @click="$emit('select', row)"
        >
          <td>
            <div class="cell-name">{{ row.nickname || '未识别昵称' }}</div>
            <div class="cell-sub">{{ row.source || 'wechat' }}</div>
          </td>
          <td>{{ row.wechat_id || '—' }}</td>
          <td>{{ row.phone || '—' }}</td>
          <td>{{ row.region || '—' }}</td>
          <td>{{ row.remark || '—' }}</td>
          <td>
            <span class="tag" :class="`tag--${row.confidence}`">{{ confidenceText(row.confidence) }}</span>
          </td>
          <td class="cell-time">{{ shortTime(row.scanned_at) }}</td>
        </tr>
        <tr v-if="!filtered.length">
          <td colspan="7" class="empty-cell">{{ emptyText }}</td>
        </tr>
      </tbody>
    </table>

    <div v-if="filtered.length > pageSize" class="pager">
      <button class="btn btn--ghost btn--sm" :disabled="page === 1" @click="page -= 1">上一页</button>
      <span>{{ page }} / {{ totalPages }}</span>
      <button class="btn btn--ghost btn--sm" :disabled="page === totalPages" @click="page += 1">
        下一页
      </button>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'

const props = defineProps({
  rows: { type: Array, default: () => [] },
  exportUrl: { type: String, default: '' },
  selectedId: { type: [Number, String], default: '' },
  emptyText: { type: String, default: '还没有采集到联系人' },
})

defineEmits(['select'])

const pageSize = 20
const keyword = ref('')
const page = ref(1)

const filtered = computed(() => {
  const key = keyword.value.trim().toLowerCase()
  if (!key) return props.rows
  return props.rows.filter((row) =>
    [row.nickname, row.wechat_id, row.phone, row.remark, row.region]
      .filter(Boolean)
      .some((value) => String(value).toLowerCase().includes(key)),
  )
})

const totalPages = computed(() => Math.max(1, Math.ceil(filtered.value.length / pageSize)))
const paged = computed(() => {
  const start = (page.value - 1) * pageSize
  return filtered.value.slice(start, start + pageSize)
})

// 搜索条件或数据量变化时回到第一页，避免停在空页上
watch([keyword, () => props.rows.length], () => {
  page.value = 1
})

function confidenceText(value) {
  return { high: '高', medium: '中', low: '低' }[value] || value || '—'
}

function shortTime(value) {
  return value ? String(value).replace('T', ' ').slice(5, 16) : '—'
}
</script>

<style scoped>
.candidate-table {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.candidate-table__toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
}

.candidate-table__count {
  color: var(--muted, #6b7280);
  font-size: 12px;
}

table {
  width: 100%;
  table-layout: fixed;
  border-collapse: collapse;
  font-size: 12px;
}

th,
td {
  padding: 9px 10px;
  border-bottom: 1px solid var(--line, #e3e6eb);
  text-align: left;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* 固定列宽，避免长微信号把表格撑出横向滚动条 */
th:nth-child(1) {
  width: 21%;
}
th:nth-child(2) {
  width: 19%;
}
th:nth-child(3) {
  width: 13%;
}
th:nth-child(4) {
  width: 12%;
}
th:nth-child(5) {
  width: 15%;
}
th:nth-child(6) {
  width: 8%;
}
th:nth-child(7) {
  width: 12%;
}

th {
  color: var(--muted, #6b7280);
  font-weight: 500;
  background: #fafbfc;
}

tbody tr {
  cursor: pointer;
}

tbody tr:hover {
  background: #f6f8fa;
}

tbody tr.is-active {
  background: var(--brand-soft, #e6f4ef);
}

.cell-name {
  color: var(--text, #1f2329);
  font-weight: 500;
}

.cell-sub {
  color: var(--muted, #6b7280);
  font-size: 11px;
}

.cell-time {
  color: var(--muted, #6b7280);
}

.empty-cell {
  height: 220px;
  padding: 24px;
  text-align: center;
  vertical-align: middle;
  color: var(--muted, #6b7280);
}

.pager {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 10px;
  color: var(--muted, #6b7280);
  font-size: 12px;
}
</style>
