<template>
  <div class="view">
    <header class="view__head">
      <div>
        <h1>直接添加好友</h1>
        <p class="view__sub">输入微信号或手机号，程序到微信里搜索核验，确认后点击发送申请</p>
      </div>
    </header>

    <div v-if="errorMessage" class="banner banner--error">{{ errorMessage }}</div>
    <div v-if="noticeMessage" class="banner banner--notice">{{ noticeMessage }}</div>

    <section class="card">
      <div class="card__head">
        <h2>添加对象</h2>
        <span class="hint">微信号和手机号至少填一个</span>
      </div>

      <div class="form-row">
        <label class="field">
          <span>微信号</span>
          <input v-model.trim="form.wechat_id" placeholder="例如 Z25713a" />
        </label>
        <label class="field">
          <span>手机号</span>
          <input v-model.trim="form.phone" placeholder="11 位手机号" />
        </label>
        <label class="field">
          <span>备注名（可选）</span>
          <input v-model.trim="form.name" placeholder="方便在列表里认人" />
        </label>
        <button class="btn btn--primary" :disabled="busy || !canSubmit" @click="startAdd">
          {{ busy ? '处理中…' : '核验并创建任务' }}
        </button>
      </div>

      <p class="hint hint--block">
        微信保持登录即可。程序会自己打开「添加朋友」窗口、把搜索词填进去并点搜索，读到结果后再由你确认是否发送申请。
      </p>
    </section>

    <section v-if="task" class="card">
      <div class="card__head">
        <h2>核验结果</h2>
        <span class="tag" :class="`tag--${taskStatusTone}`">{{ statusText(task.status) }}</span>
        <span class="hint">任务 #{{ task.id }}</span>
      </div>

      <dl class="result">
        <div>
          <dt>搜索依据</dt>
          <dd>{{ task.search_key || '—' }}</dd>
        </div>
        <div v-if="verification">
          <dt>核验结论</dt>
          <dd>{{ outcomeText(verification.outcome) }} · {{ verification.reason }}</dd>
        </div>
        <div v-if="verification && verification.suggested_status">
          <dt>建议状态</dt>
          <dd>{{ statusText(verification.suggested_status) }}</dd>
        </div>
      </dl>

      <ul v-if="verification && verification.entries.length" class="entries">
        <li v-for="(entry, index) in verification.entries.slice(0, 6)" :key="index">
          {{ entry.raw_text }}
        </li>
      </ul>

      <div class="actions">
        <button v-if="canSend" class="btn btn--primary" :disabled="busy" @click="sendRequest">
          自动点击发送申请
        </button>
        <button class="btn btn--ghost" :disabled="busy" @click="refreshTask">刷新状态</button>
      </div>
    </section>

    <section class="card">
      <div class="card__head">
        <h2>最近添加记录</h2>
        <span class="hint">{{ recentTasks.length }} 条</span>
      </div>
      <table v-if="recentTasks.length" class="recent">
        <thead>
          <tr>
            <th>任务</th>
            <th>搜索依据</th>
            <th>状态</th>
            <th>创建时间</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in recentTasks" :key="item.id" @click="selectTask(item)">
            <td>#{{ item.id }}</td>
            <td>{{ item.search_key || '—' }}</td>
            <td>
              <span class="tag" :class="`tag--${tone(item.status)}`">{{ statusText(item.status) }}</span>
            </td>
            <td class="cell-time">{{ shortTime(item.created_at) }}</td>
          </tr>
        </tbody>
      </table>
      <div v-else class="empty">还没有添加记录</div>
    </section>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'

const props = defineProps({
  apiBase: { type: String, default: 'http://127.0.0.1:8756' },
})

const form = ref({ wechat_id: '', phone: '', name: '' })
const task = ref(null)
const tasks = ref([])
const busy = ref(false)
const errorMessage = ref('')
const noticeMessage = ref('')

const STATUS_LABELS = {
  pending: '待处理',
  reviewing: '核验中',
  pending_confirmation: '待确认',
  submitted: '已提交待通过',
  accepted: '已成为好友',
  not_found: '未找到',
  ambiguous: '多结果待定',
  failed: '失败',
  cancelled: '已取消',
}

const OUTCOME_LABELS = {
  matched: '结果一致',
  not_found: '未找到用户',
  ambiguous: '多结果待定',
  needs_review: '需人工核对',
}

const canSubmit = computed(() => Boolean(form.value.wechat_id || form.value.phone))
const verification = computed(() => task.value?.evidence?.verification || null)
const canSend = computed(
  () => task.value?.status === 'pending_confirmation' && verification.value?.outcome === 'matched',
)
const taskStatusTone = computed(() => tone(task.value?.status))
const recentTasks = computed(() => tasks.value.slice(0, 12))

function tone(status) {
  if (status === 'accepted') return 'high'
  if (status === 'pending_confirmation' || status === 'submitted') return 'medium'
  if (status === 'not_found' || status === 'failed' || status === 'ambiguous') return 'low'
  return ''
}

function statusText(value) {
  return STATUS_LABELS[value] || value || '—'
}

function outcomeText(value) {
  return OUTCOME_LABELS[value] || value || '—'
}

function shortTime(value) {
  return value ? String(value).replace('T', ' ').slice(5, 16) : '—'
}

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

async function loadTasks() {
  try {
    const body = await request('/api/tasks')
    tasks.value = body.tasks
  } catch {
    /* 列表加载失败不影响主流程 */
  }
}

async function startAdd() {
  errorMessage.value = ''
  noticeMessage.value = ''
  busy.value = true
  try {
    const searchKey = form.value.wechat_id || form.value.phone
    const crmId = `wx${Date.now()}`

    // 有微信号或手机号就能先建 CRM 联系人，再挂一条好友申请任务
    const contactBody = await request('/api/contacts', {
      method: 'POST',
      body: JSON.stringify({
        crm_contact_id: crmId,
        name: form.value.name || searchKey,
        phone: form.value.phone,
        wechat_id: form.value.wechat_id,
      }),
    })

    const taskBody = await request('/api/tasks', {
      method: 'POST',
      body: JSON.stringify({
        crm_contact_id: contactBody.contact.crm_contact_id,
        search_key: searchKey,
      }),
    })

    const created = taskBody.task
    const searchBody = await request(`/api/tasks/${created.id}/search`, {
      method: 'POST',
      body: JSON.stringify({ auto_input: true, confirmed: true }),
    })

    task.value = searchBody.task || created
    // 核验一致后把任务推进到「待确认」，否则发送接口不接受这个状态
    if (searchBody.verification?.outcome === 'matched' && task.value.status === 'reviewing') {
      const moved = await request(`/api/tasks/${created.id}/transition`, {
        method: 'POST',
        body: JSON.stringify({
          to_status: 'pending_confirmation',
          actor: 'user',
          note: '搜索核验一致，等待确认发送',
          confirmed: true,
        }),
      })
      task.value = moved.task
    }
    noticeMessage.value = `已核验：${outcomeText(searchBody.verification?.outcome)}`
    await loadTasks()
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    busy.value = false
  }
}

async function sendRequest() {
  if (!task.value) return
  const agreed = window.confirm('将由程序控制鼠标点击「添加到通讯录」和「确定」，只发送这一条申请。确认继续？')
  if (!agreed) return
  busy.value = true
  try {
    await request(`/api/tasks/${task.value.id}/send`, {
      method: 'POST',
      body: JSON.stringify({ confirmed: true }),
    })
    await refreshTask()
    noticeMessage.value = `任务 #${task.value.id} 已发送好友申请`
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    busy.value = false
  }
}

async function refreshTask() {
  if (!task.value) return
  try {
    const body = await request(`/api/tasks/${task.value.id}`)
    task.value = body.task
    await loadTasks()
  } catch (error) {
    errorMessage.value = error.message
  }
}

function selectTask(item) {
  task.value = item
}

onMounted(loadTasks)
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

.card {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 10px;
  padding: 14px 16px;
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
  margin: 12px 0 0;
}

.form-row {
  display: flex;
  align-items: flex-end;
  gap: 14px;
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
  width: 190px;
  padding: 6px 9px;
  border: 1px solid var(--line);
  border-radius: 6px;
  font-size: 12px;
}

.result {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  margin: 0;
}

.result > div {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.result dt {
  color: var(--muted);
  font-size: 11px;
}

.result dd {
  margin: 0;
  font-size: 12px;
}

.entries {
  margin: 12px 0 0;
  padding-left: 18px;
  color: var(--muted);
  font-size: 12px;
}

.actions {
  display: flex;
  gap: 8px;
  margin-top: 14px;
}

.recent {
  width: 100%;
  table-layout: fixed;
  border-collapse: collapse;
  font-size: 12px;
}

.recent th,
.recent td {
  padding: 9px 10px;
  border-bottom: 1px solid var(--line);
  text-align: left;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.recent th {
  color: var(--muted);
  font-weight: 500;
  background: #fafbfc;
}

.recent tbody tr {
  cursor: pointer;
}

.recent tbody tr:hover {
  background: #f6f8fa;
}

.cell-time {
  color: var(--muted);
}

.empty {
  padding: 24px;
  text-align: center;
  color: var(--muted);
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
