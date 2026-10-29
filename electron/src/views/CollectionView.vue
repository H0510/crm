<template>
  <div class="view">
    <header class="view__head">
      <div>
        <h1>通讯录采集</h1>
        <p class="view__sub">逐个打开微信资料卡，读取微信号、手机号和地区</p>
      </div>
      <div class="view__aside">
        <span class="pill" :class="statusPillClass">{{ statusText }}</span>
        <button class="btn btn--ghost" :disabled="busy" @click="refreshAll">刷新</button>
      </div>
    </header>

    <div v-if="errorMessage" class="banner banner--error">{{ errorMessage }}</div>
    <div v-if="noticeMessage" class="banner banner--notice">{{ noticeMessage }}</div>

    <section class="card">
      <div class="card__head">
        <h2>采集任务</h2>
        <span class="hint">{{ collectStateText }}</span>
      </div>

      <div class="form-row">
        <label class="field">
          <span>采集人数上限</span>
          <input v-model.number="form.max_contacts" type="number" min="1" max="3000" />
        </label>
        <label class="field">
          <span>每行等待（秒）</span>
          <input v-model.number="form.settle_seconds" type="number" min="0.3" max="2" step="0.1" />
        </label>
        <div class="form-row__actions">
          <button class="btn btn--primary" :disabled="running || busy" @click="startCollect">
            开始采集
          </button>
          <button class="btn btn--ghost" :disabled="!running" @click="cancelCollect">取消</button>
        </div>
      </div>

      <div v-if="job" class="progress">
        <div class="progress__bar">
          <span :style="{ width: `${percent}%` }"></span>
        </div>
        <p class="progress__meta">
          已采 {{ job.collected || 0 }} · 新增 {{ job.created || 0 }} · 更新 {{ job.updated || 0 }} ·
          已扫描 {{ job.scanned || 0 }} 行
          <template v-if="job.current_nickname"> · 当前：{{ job.current_nickname }}</template>
        </p>
        <p v-if="job.last_error" class="progress__error">{{ job.last_error }}</p>
      </div>
      <p v-else class="hint hint--block">
        开始前微信需要停在「通讯录」页；程序会自动切页，并在窗口过窄时把窗口撑到三栏布局。
      </p>
    </section>

    <section class="card card--grow">
      <div class="card__head">
        <h2>采集到的联系人</h2>
        <span class="hint">{{ candidates.length }} 条</span>
      </div>
      <CandidateTable
        :rows="candidates"
        :export-url="`${apiBase}/api/candidates/export.csv`"
        empty-text="还没有采集到联系人，先跑一次采集"
      />
    </section>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import CandidateTable from '../components/CandidateTable.vue'

const props = defineProps({
  apiBase: { type: String, default: 'http://127.0.0.1:8756' },
})

const status = ref({ running: false, login_state: 'unknown', main_window: null })
const candidates = ref([])
const form = ref({ max_contacts: 200, settle_seconds: 0.6 })
const job = ref(null)
const busy = ref(false)
const errorMessage = ref('')
const noticeMessage = ref('')
let timer = null

const running = computed(() => job.value?.state === 'running')
const percent = computed(() => {
  if (!job.value) return 0
  const total = job.value.max_contacts || 1
  const done = job.value.collected ?? 0
  return Math.min(100, Math.round((done / total) * 100))
})

const STATE_LABELS = {
  running: '采集中',
  completed: '已完成',
  cancelled: '已取消',
  failed: '失败',
}
const collectStateText = computed(() => (job.value ? STATE_LABELS[job.value.state] || job.value.state : '未开始'))

const statusText = computed(() => {
  if (!status.value.running) return '微信未运行'
  if (status.value.login_state === 'logged_in') return '微信已登录'
  if (status.value.login_state === 'not_logged_in') return '微信未登录'
  return '登录状态待确认'
})
const statusPillClass = computed(() => {
  if (!status.value.running) return 'pill--off'
  return status.value.login_state === 'logged_in' ? 'pill--on' : 'pill--warn'
})

async function request(path, options = {}) {
  const response = await fetch(`${props.apiBase}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}))
    throw new Error(detail.detail || `请求失败：${response.status}`)
  }
  return response.json()
}

async function loadCandidates() {
  try {
    const body = await request('/api/candidates')
    candidates.value = body.candidates
  } catch {
    /* 采集结束刷新候选失败时不影响主流程 */
  }
}

async function refreshAll() {
  busy.value = true
  errorMessage.value = ''
  try {
    const [statusBody, candidateBody] = await Promise.all([
      request('/api/wechat/status'),
      request('/api/candidates'),
    ])
    status.value = statusBody
    candidates.value = candidateBody.candidates
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    busy.value = false
  }
}

function stopTimer() {
  if (timer) {
    clearInterval(timer)
    timer = null
  }
}

function applyJob(next) {
  job.value = next
  if (next.state === 'running') return
  stopTimer()
  loadCandidates()
}

async function startCollect() {
  errorMessage.value = ''
  noticeMessage.value = ''
  busy.value = true
  try {
    const body = await request('/api/collect/address-book', {
      method: 'POST',
      body: JSON.stringify({
        max_contacts: form.value.max_contacts,
        settle_seconds: form.value.settle_seconds,
      }),
    })
    applyJob(body.job)
    stopTimer()
    timer = setInterval(async () => {
      if (!job.value) return
      try {
        const current = await request(`/api/collect/jobs/${job.value.job_id}`)
        applyJob(current.job)
      } catch (error) {
        stopTimer()
        errorMessage.value = error.message
      }
    }, 2000)
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    busy.value = false
  }
}

async function cancelCollect() {
  if (!job.value) return
  try {
    const body = await request(`/api/collect/jobs/${job.value.job_id}/cancel`, {
      method: 'POST',
      body: '{}',
    })
    applyJob(body.job)
  } catch (error) {
    errorMessage.value = error.message
  }
}

onMounted(refreshAll)
onUnmounted(stopTimer)
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

.view__aside {
  display: flex;
  align-items: center;
  gap: 10px;
}

.card {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 10px;
  padding: 14px 16px;
}

.card--grow {
  flex: 0 0 auto;
}

.card__head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
}

.card__head h2 {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
}

.hint {
  color: var(--muted);
  font-size: 12px;
}

.hint--block {
  margin: 10px 0 0;
}

.form-row {
  display: flex;
  align-items: flex-end;
  gap: 16px;
  flex-wrap: wrap;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 5px;
  color: var(--muted);
  font-size: 12px;
}

.field input {
  width: 120px;
  padding: 6px 9px;
  border: 1px solid var(--line);
  border-radius: 6px;
  font-size: 12px;
}

.form-row__actions {
  display: flex;
  gap: 8px;
}

.progress {
  margin-top: 14px;
}

.progress__bar {
  height: 6px;
  border-radius: 999px;
  background: #eef1f5;
  overflow: hidden;
}

.progress__bar span {
  display: block;
  height: 100%;
  background: var(--brand);
  transition: width 0.4s ease;
}

.progress__meta {
  margin: 8px 0 0;
  color: var(--muted);
  font-size: 12px;
}

.progress__error {
  margin: 6px 0 0;
  color: var(--danger);
  font-size: 12px;
}

.banner {
  padding: 8px 12px;
  border-radius: 8px;
  font-size: 12px;
}

.banner--error {
  background: #f8ebea;
  color: var(--danger);
}

.banner--notice {
  background: var(--brand-soft);
  color: var(--brand);
}
</style>
