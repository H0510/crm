<template>
  <div class="view">
    <header class="view__head">
      <div>
        <h1>联系人库</h1>
        <p class="view__sub">通讯录采集到的微信号、手机号与地区，按采集时间倒序</p>
      </div>
      <div class="view__aside">
        <button class="btn btn--ghost" :disabled="busy" @click="refreshAll">刷新</button>
      </div>
    </header>

    <div v-if="errorMessage" class="banner banner--error">{{ errorMessage }}</div>

    <div class="layout">
      <section class="card">
        <CandidateTable
          :rows="candidates"
          :export-url="`${apiBase}/api/candidates/export.csv`"
          :selected-id="selected?.id ?? ''"
          empty-text="还没有采集到联系人"
          @select="selected = $event"
        />
      </section>

      <aside class="card card--detail">
        <template v-if="selected">
          <h2>{{ selected.nickname || '未识别昵称' }}</h2>
          <dl>
            <div>
              <dt>微信号</dt>
              <dd>{{ selected.wechat_id || '—' }}</dd>
            </div>
            <div>
              <dt>手机号</dt>
              <dd>{{ selected.phone || '—' }}</dd>
            </div>
            <div>
              <dt>地区</dt>
              <dd>{{ selected.region || '—' }}</dd>
            </div>
            <div>
              <dt>备注</dt>
              <dd>{{ selected.remark || '—' }}</dd>
            </div>
            <div>
              <dt>邮箱</dt>
              <dd>{{ selected.email || '—' }}</dd>
            </div>
            <div>
              <dt>置信度</dt>
              <dd>{{ selected.confidence || '—' }}</dd>
            </div>
            <div>
              <dt>采集时间</dt>
              <dd>{{ shortTime(selected.scanned_at) }}</dd>
            </div>
            <div>
              <dt>去重标识</dt>
              <dd class="mono">{{ selected.profile_key || '—' }}</dd>
            </div>
          </dl>
        </template>
        <div v-else class="empty">左侧选一位联系人查看详情</div>
      </aside>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import CandidateTable from '../components/CandidateTable.vue'

const props = defineProps({
  apiBase: { type: String, default: 'http://127.0.0.1:8756' },
})

const candidates = ref([])
const selected = ref(null)
const busy = ref(false)
const errorMessage = ref('')

async function refreshAll() {
  busy.value = true
  errorMessage.value = ''
  try {
    const response = await fetch(`${props.apiBase}/api/candidates`)
    if (!response.ok) {
      const detail = await response.json().catch(() => ({}))
      throw new Error(detail.detail || `请求失败：${response.status}`)
    }
    const body = await response.json()
    candidates.value = body.candidates
    if (selected.value) {
      selected.value = candidates.value.find((row) => row.id === selected.value.id) || null
    }
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    busy.value = false
  }
}

function shortTime(value) {
  return value ? String(value).replace('T', ' ').slice(0, 16) : '—'
}

onMounted(refreshAll)
</script>

<style scoped>
.view {
  display: flex;
  flex-direction: column;
  gap: 14px;
  height: 100%;
  padding: 18px 22px;
  box-sizing: border-box;
  overflow: auto;
}

.view__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.view__head h1 {
  margin: 0;
  font-size: 17px;
  font-weight: 600;
}

.view__sub {
  margin: 4px 0 0;
  color: var(--muted);
  font-size: 12px;
}

.layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 300px;
  gap: 14px;
  flex: 1;
  min-height: 0;
}

.card {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 10px;
  padding: 14px 16px;
  overflow: auto;
}

.card--detail h2 {
  margin: 0 0 12px;
  font-size: 15px;
  font-weight: 600;
}

dl {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin: 0;
}

dl > div {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

dt {
  color: var(--muted);
  font-size: 11px;
}

dd {
  margin: 0;
  font-size: 12px;
  word-break: break-all;
}

.mono {
  font-family: Consolas, monospace;
  font-size: 11px;
}

.empty {
  color: var(--muted);
  font-size: 12px;
  padding: 20px 0;
  text-align: center;
}

.banner {
  padding: 8px 12px;
  border-radius: 8px;
  font-size: 12px;
  background: #f8ebea;
  color: var(--danger);
}
</style>
