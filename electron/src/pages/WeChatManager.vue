<template>
  <div class="wechat-manager">
    <header class="topbar">
      <div class="topbar__identity">
        <div class="topbar__logo">微信获客</div>
        <div class="topbar__status">
          <span class="pill" :class="statusPillClass">
            <i class="pill__dot"></i>
            {{ statusText }}
          </span>
          <span class="topbar__window">{{ mainWindowLabel }}</span>
          <span v-if="status.account" class="topbar__account">
            当前账号：{{ status.account }}
          </span>
        </div>
      </div>
      <div class="topbar__actions">
        <button class="btn btn--ghost" :disabled="busy" @click="refreshAll">刷新</button>
        <button class="btn btn--primary" :disabled="busy" @click="scanWechat">读取微信界面</button>
      </div>
    </header>

    <div v-if="errorMessage" class="banner banner--error">{{ errorMessage }}</div>
    <div v-if="noticeMessage" class="banner banner--notice">{{ noticeMessage }}</div>

    <main class="layout">
      <section class="panel panel--list">
        <div class="panel__head">
          <h2>CRM 联系人</h2>
          <span class="panel__hint">{{ contacts.length }} 位</span>
        </div>

        <div class="filter-row">
          <input v-model="keyword" class="input" placeholder="按姓名、手机号、微信号筛选" />
          <select v-model="matchFilter" class="input input--select">
            <option value="">全部匹配状态</option>
            <option value="matched">已匹配</option>
            <option value="multiple_matches">多候选</option>
            <option value="unmatched">未匹配</option>
          </select>
        </div>

        <table class="contact-table">
          <thead>
            <tr>
              <th>客户</th>
              <th>手机号</th>
              <th>微信号</th>
              <th>匹配</th>
              <th>好友</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="contact in filteredContacts"
              :key="contact.crm_contact_id"
              :class="{ 'is-active': selectedId === contact.crm_contact_id }"
              @click="selectContact(contact)"
            >
              <td>
                <div class="contact-name">{{ contact.name || contact.crm_contact_id }}</div>
                <div class="contact-sub">{{ contact.crm_contact_id }}</div>
              </td>
              <td>{{ contact.phone || '—' }}</td>
              <td>{{ contact.wechat_id || '—' }}</td>
              <td>
                <span class="tag" :class="`tag--${contact.match_status}`">
                  {{ matchText(contact.match_status) }}
                </span>
              </td>
              <td>{{ friendText(contact.friend_status) }}</td>
            </tr>
            <tr v-if="!filteredContacts.length">
              <td colspan="5" class="empty-cell">暂无联系人数据</td>
            </tr>
          </tbody>
        </table>
      </section>

      <aside class="panel panel--detail">
        <template v-if="selectedContact">
          <div class="panel__head">
            <h2>{{ selectedContact.name || selectedContact.crm_contact_id }}</h2>
            <button class="btn btn--ghost btn--sm" :disabled="busy" @click="matchSelected">
              重新匹配
            </button>
          </div>

          <dl class="detail-grid">
            <div>
              <dt>手机号</dt>
              <dd>{{ selectedContact.phone || '—' }}</dd>
            </div>
            <div>
              <dt>微信号</dt>
              <dd>{{ selectedContact.wechat_id || '—' }}</dd>
            </div>
            <div>
              <dt>微信昵称</dt>
              <dd>{{ selectedContact.wechat_nickname || '—' }}</dd>
            </div>
            <div>
              <dt>备注</dt>
              <dd>{{ selectedContact.wechat_remark || '—' }}</dd>
            </div>
            <div class="detail-grid__full">
              <dt>最近错误</dt>
              <dd>{{ selectedContact.last_error || '—' }}</dd>
            </div>
          </dl>

          <section class="block">
            <div class="block__head">
              <h3>好友申请任务</h3>
              <button
                class="btn btn--ghost btn--sm"
                :disabled="busy || !selectedContact"
                @click="createTask"
              >
                新建任务
              </button>
            </div>

            <div v-if="!currentTasks.length" class="empty-cell">该联系人还没有任务</div>

            <article v-for="task in currentTasks" :key="task.id" class="task-card">
              <div class="task-card__head">
                <span class="tag" :class="`tag--${task.status}`">{{ taskText(task.status) }}</span>
                <span class="task-card__id">#{{ task.id }}</span>
              </div>
              <div class="task-card__meta">
                搜索依据：{{ task.search_key || '—' }}
              </div>
              <div class="task-card__search">
                <button
                  class="btn btn--sm btn--ghost"
                  :disabled="busy"
                  @click="searchTask(task, false)"
                >
                  复制搜索词并读取结果
                </button>
                <button
                  class="btn btn--sm btn--ghost"
                  :disabled="busy"
                  @click="searchTask(task, true)"
                >
                  自动输入并读取结果
                </button>
              </div>

              <div v-if="verificationOf(task)" class="verify">
                <div class="verify__head">
                  <span class="tag" :class="`tag--${verificationOf(task).outcome}`">
                    {{ outcomeText(verificationOf(task).outcome) }}
                  </span>
                  <span class="verify__time">
                    {{ shortTime(verificationOf(task).checked_at) }}
                  </span>
                </div>
                <div class="verify__reason">{{ verificationOf(task).reason }}</div>
                <ul v-if="verificationOf(task).entries.length" class="verify__entries">
                  <li v-for="(entry, index) in verificationOf(task).entries.slice(0, 6)" :key="index">
                    {{ entry.raw_text }}
                  </li>
                </ul>
                <div v-if="verificationOf(task).suggested_status" class="verify__suggest">
                  建议操作：{{ taskText(verificationOf(task).suggested_status) }}
                </div>
              </div>
              <div class="task-card__actions">
                <button
                  v-if="canAutoSend(task)"
                  class="btn btn--sm btn--primary"
                  :disabled="busy"
                  @click="runSend(task)"
                >
                  自动点击发送
                </button>
                <button
                  v-for="action in availableActions(task)"
                  :key="action.to"
                  class="btn btn--sm"
                  :class="action.primary ? 'btn--primary' : 'btn--ghost'"
                  :disabled="busy"
                  @click="runTransition(task, action)"
                >
                  {{ action.label }}
                </button>
              </div>
            </article>
          </section>

          <section class="block">
            <div class="block__head">
              <h3>操作记录</h3>
            </div>
            <ol v-if="events.length" class="timeline">
              <li v-for="event in events" :key="event.id">
                <span class="timeline__time">{{ shortTime(event.created_at) }}</span>
                <span class="timeline__text">
                  {{ taskText(event.from_status) }} → {{ taskText(event.to_status) }}
                </span>
                <span class="timeline__note">{{ event.note }}</span>
              </li>
            </ol>
            <div v-else class="empty-cell">暂无操作记录</div>
          </section>

          <section class="block">
            <div class="block__head">
              <h3>界面读取结果</h3>
              <span class="panel__hint">{{ candidates.length }} 条候选</span>
            </div>
            <ul v-if="candidates.length" class="candidate-list">
              <li v-for="item in candidates.slice(0, 8)" :key="`${item.id}`">
                <span class="candidate-list__raw">{{ item.raw_text }}</span>
                <span class="candidate-list__meta">
                  {{ item.phone || item.wechat_id || item.nickname || '—' }} ·
                  {{ confidenceText(item.confidence) }}
                </span>
              </li>
            </ul>
            <div v-else class="empty-cell">尚未读取微信界面，点击右上角「读取微信界面」</div>

            <img v-if="screenshotUrl" :src="screenshotUrl" class="shot" alt="微信窗口截图" />
          </section>
        </template>
        <div v-else class="empty-state">左侧选择一位 CRM 联系人</div>
      </aside>
    </main>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'

const props = defineProps({
  apiBase: { type: String, default: 'http://127.0.0.1:8756' },
})

const status = ref({
  running: false,
  login_state: 'unknown',
  account: '',
  windows: [],
  evidence: [],
})
const contacts = ref([])
const tasks = ref([])
const events = ref([])
const candidates = ref([])
const selectedId = ref('')
const keyword = ref('')
const matchFilter = ref('')
const busy = ref(false)
const errorMessage = ref('')
const noticeMessage = ref('')
const screenshotUrl = ref('')

const TASK_LABELS = {
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

const MATCH_LABELS = {
  matched: '已匹配',
  multiple_matches: '多候选',
  unmatched: '未匹配',
}

const FRIEND_LABELS = {
  none: '未处理',
  pending_review: '待核验',
  submitted: '已提交',
  accepted: '已是好友',
}

const OUTCOME_LABELS = {
  matched: '结果一致',
  not_found: '未找到用户',
  ambiguous: '多结果待定',
  needs_review: '需人工核对',
}

const ACTIONS = {
  pending: [
    { to: 'reviewing', label: '开始核验' },
    { to: 'cancelled', label: '取消' },
  ],
  reviewing: [
    { to: 'pending_confirmation', label: '待用户确认' },
    { to: 'not_found', label: '未找到' },
    { to: 'ambiguous', label: '结果不唯一' },
    { to: 'cancelled', label: '取消' },
  ],
  pending_confirmation: [
    { to: 'submitted', label: '确认已发送', primary: true, needConfirm: true },
    { to: 'cancelled', label: '取消' },
  ],
  submitted: [
    { to: 'accepted', label: '对方已通过', primary: true },
    { to: 'failed', label: '申请失败' },
  ],
  not_found: [{ to: 'reviewing', label: '重新核验' }],
  ambiguous: [
    { to: 'reviewing', label: '重新核验' },
    { to: 'failed', label: '标记失败' },
  ],
  failed: [{ to: 'reviewing', label: '重新核验' }],
}

const selectedContact = computed(
  () => contacts.value.find((item) => item.crm_contact_id === selectedId.value) || null,
)
const currentTasks = computed(() =>
  tasks.value.filter((task) => task.crm_contact_id === selectedId.value),
)
const mainWindowLabel = computed(() => {
  const window = status.value.main_window
  if (!window) return '未找到微信窗口'
  return `${window.title || window.class_name} · ${window.visible ? '可见' : '在托盘'}`
})
const statusText = computed(() => {
  if (!status.value.running) return '微信未运行'
  if (status.value.login_state === 'logged_in') return '微信已登录'
  if (status.value.login_state === 'not_logged_in') return '微信未登录'
  return '登录状态待确认'
})
const statusPillClass = computed(() => {
  if (!status.value.running) return 'pill--off'
  if (status.value.login_state === 'logged_in') return 'pill--on'
  return 'pill--warn'
})
const filteredContacts = computed(() => {
  const key = keyword.value.trim().toLowerCase()
  return contacts.value.filter((contact) => {
    if (matchFilter.value && contact.match_status !== matchFilter.value) return false
    if (!key) return true
    return [contact.name, contact.phone, contact.wechat_id, contact.crm_contact_id]
      .filter(Boolean)
      .some((value) => String(value).toLowerCase().includes(key))
  })
})

function matchText(value) {
  return MATCH_LABELS[value] || value
}
function friendText(value) {
  return FRIEND_LABELS[value] || value
}
function taskText(value) {
  return TASK_LABELS[value] || value || '—'
}
function confidenceText(value) {
  return { high: '高置信', medium: '中置信', low: '低置信' }[value] || value
}
function outcomeText(value) {
  return OUTCOME_LABELS[value] || value
}
function verificationOf(task) {
  return task && task.evidence ? task.evidence.verification : null
}
function shortTime(value) {
  return value ? value.replace('T', ' ').slice(5, 16) : ''
}
function availableActions(task) {
  return ACTIONS[task.status] || []
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

async function refreshAll() {
  busy.value = true
  errorMessage.value = ''
  try {
    const [statusBody, contactBody, taskBody, candidateBody] = await Promise.all([
      request('/api/wechat/status', { method: 'GET' }),
      request('/api/contacts'),
      request('/api/tasks'),
      request('/api/candidates'),
    ])
    status.value = statusBody
    contacts.value = contactBody.contacts
    tasks.value = taskBody.tasks
    candidates.value = candidateBody.candidates
    if (!selectedId.value && contacts.value.length) selectContact(contacts.value[0])
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    busy.value = false
  }
}

async function scanWechat() {
  busy.value = true
  errorMessage.value = ''
  noticeMessage.value = ''
  try {
    const result = await request('/api/contacts/scan', { method: 'POST', body: '{}' })
    candidates.value = result.candidates
    noticeMessage.value = `读取到 ${result.candidates.length} 条候选，失败窗口 ${result.errors.length} 个`
    const mainWindow = status.value.main_window
    if (mainWindow) {
      screenshotUrl.value = `${props.apiBase}/api/wechat/windows/${mainWindow.hwnd}/screenshot?t=${Date.now()}`
    }
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    busy.value = false
  }
}

async function selectContact(contact) {
  selectedId.value = contact.crm_contact_id
  const windows = status.value.windows || []
  const visible = windows.find((item) => item.visible)
  screenshotUrl.value = visible
    ? `${props.apiBase}/api/wechat/windows/${visible.hwnd}/screenshot?t=${Date.now()}`
    : ''
  await loadEvents()
}

async function loadEvents() {
  const task = currentTasks.value[0]
  if (!task) {
    events.value = []
    return
  }
  try {
    const body = await request(`/api/tasks/${task.id}/events`)
    events.value = body.events
  } catch (error) {
    errorMessage.value = error.message
  }
}

async function matchSelected() {
  if (!selectedContact.value) return
  busy.value = true
  try {
    const body = await request(`/api/contacts/${selectedContact.value.crm_contact_id}/match`, {
      method: 'POST',
    })
    await refreshAll()
    noticeMessage.value = `匹配结果：${matchText(body.result.status)}`
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    busy.value = false
  }
}

async function createTask() {
  if (!selectedContact.value) return
  busy.value = true
  try {
    await request('/api/tasks', {
      method: 'POST',
      body: JSON.stringify({ crm_contact_id: selectedContact.value.crm_contact_id }),
    })
    await refreshAll()
    await loadEvents()
    noticeMessage.value = '已创建好友申请任务'
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    busy.value = false
  }
}

async function searchTask(task, autoInput) {
  if (autoInput) {
    const agreed = window.confirm(
      '将把搜索词粘贴到「添加朋友」窗口的搜索框，不会按回车或点击任何按钮。请先点一下搜索框让光标在里面，确认继续？',
    )
    if (!agreed) return
  }
  busy.value = true
  errorMessage.value = ''
  noticeMessage.value = ''
  try {
    const body = await request(`/api/tasks/${task.id}/search`, {
      method: 'POST',
      body: JSON.stringify({ auto_input: autoInput, confirmed: autoInput }),
    })
    noticeMessage.value = `核验结果：${outcomeText(body.verification.outcome)}（${body.verification.reason}）`
    await refreshAll()
    await loadEvents()
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    busy.value = false
  }
}

function canAutoSend(task) {
  return task.status === 'pending_confirmation' && verificationOf(task).outcome === 'matched'
}

async function runSend(task) {
  const agreed = window.confirm(
    '将由 Python 控制鼠标点击「添加到通讯录」和「确定」，只发送这一条好友申请。确认继续？',
  )
  if (!agreed) return
  busy.value = true
  try {
    await request(`/api/tasks/${task.id}/send`, {
      method: 'POST',
      body: JSON.stringify({ confirmed: true }),
    })
    await refreshAll()
    await loadEvents()
    noticeMessage.value = `任务 #${task.id} 已由 Python 点击发送`
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    busy.value = false
  }
}

async function runTransition(task, action) {
  if (action.needConfirm) {
    const agreed = window.confirm('确认已在微信中发送好友申请？该操作会记录为「已提交」')
    if (!agreed) return
  }
  busy.value = true
  try {
    await request(`/api/tasks/${task.id}/transition`, {
      method: 'POST',
      body: JSON.stringify({
        to_status: action.to,
        actor: 'user',
        confirmed: Boolean(action.needConfirm),
        note: action.label,
      }),
    })
    await refreshAll()
    await loadEvents()
    noticeMessage.value = `任务 #${task.id} 已更新为「${taskText(action.to)}」`
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    busy.value = false
  }
}

onMounted(refreshAll)
</script>

<style scoped>
.wechat-manager {
  --bg: #f6f7f9;
  --panel: #ffffff;
  --line: #e3e6eb;
  --text: #1f2329;
  --muted: #6b7280;
  --brand: #1f7a5c;
  --brand-soft: #e6f4ef;
  --warn: #b7791f;
  --danger: #c0392b;
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 640px;
  background: var(--bg);
  color: var(--text);
  font-size: 13px;
}

.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 20px;
  background: var(--panel);
  border-bottom: 1px solid var(--line);
}

.topbar__identity {
  display: flex;
  align-items: center;
  gap: 18px;
}

.topbar__logo {
  font-size: 16px;
  font-weight: 600;
  letter-spacing: 0.5px;
}

.topbar__status {
  display: flex;
  align-items: center;
  gap: 10px;
}

.topbar__window {
  color: var(--muted);
  font-size: 12px;
}

.topbar__account {
  padding: 3px 9px;
  border-radius: 6px;
  background: #f2f6f4;
  color: var(--brand);
  font-size: 12px;
}

.topbar__actions {
  display: flex;
  gap: 8px;
}

.pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border-radius: 999px;
  font-size: 12px;
  background: #eef1f5;
  color: var(--muted);
}

.pill__dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
}

.pill--on {
  background: var(--brand-soft);
  color: var(--brand);
}

.pill--warn {
  background: #fdf3e3;
  color: var(--warn);
}

.pill--off {
  background: #f1f2f4;
  color: var(--muted);
}

.banner {
  padding: 9px 20px;
  font-size: 12px;
  border-bottom: 1px solid var(--line);
}

.banner--error {
  background: #fdecea;
  color: var(--danger);
}

.banner--notice {
  background: var(--brand-soft);
  color: var(--brand);
}

.layout {
  flex: 1;
  display: grid;
  grid-template-columns: minmax(420px, 1.35fr) minmax(340px, 1fr);
  gap: 16px;
  padding: 16px 20px 24px;
  overflow: hidden;
}

.panel {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 10px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.panel--list {
  min-height: 0;
}

.panel--detail {
  padding: 16px 18px;
  overflow-y: auto;
}

.panel__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 16px 10px;
}

.panel--detail .panel__head {
  padding: 0 0 12px;
}

.panel__head h2 {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
}

.panel__hint {
  color: var(--muted);
  font-size: 12px;
}

.filter-row {
  display: grid;
  grid-template-columns: 1fr 150px;
  gap: 8px;
  padding: 0 16px 12px;
}

.input {
  height: 30px;
  padding: 0 10px;
  border: 1px solid var(--line);
  border-radius: 6px;
  font-size: 12px;
  color: var(--text);
  background: #fff;
  outline: none;
}

.input:focus {
  border-color: var(--brand);
}

.input--select {
  cursor: pointer;
}

.contact-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
  overflow-y: auto;
}

.contact-table thead th {
  position: sticky;
  top: 0;
  text-align: left;
  padding: 8px 12px;
  background: #fafbfc;
  border-top: 1px solid var(--line);
  border-bottom: 1px solid var(--line);
  font-weight: 500;
  color: var(--muted);
}

.contact-table tbody td {
  padding: 10px 12px;
  border-bottom: 1px solid #f0f2f5;
}

.contact-table tbody tr {
  cursor: pointer;
  transition: background 0.15s ease;
}

.contact-table tbody tr:hover {
  background: #fafbfc;
}

.contact-table tbody tr.is-active {
  background: var(--brand-soft);
}

.contact-name {
  font-weight: 500;
}

.contact-sub {
  color: var(--muted);
  font-size: 11px;
  margin-top: 2px;
}

.empty-cell {
  padding: 18px 12px;
  text-align: center;
  color: var(--muted);
  font-size: 12px;
}

.empty-state {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: var(--muted);
}

.tag {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 11px;
  background: #f1f2f4;
  color: var(--muted);
}

.tag--matched,
.tag--accepted {
  background: var(--brand-soft);
  color: var(--brand);
}

.tag--multiple_matches,
.tag--ambiguous,
.tag--pending_confirmation,
.tag--needs_review {
  background: #fdf3e3;
  color: var(--warn);
}

.tag--unmatched,
.tag--failed,
.tag--not_found {
  background: #fdecea;
  color: var(--danger);
}

.detail-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px 14px;
  margin: 0 0 14px;
}

.detail-grid dt {
  color: var(--muted);
  font-size: 11px;
  margin-bottom: 2px;
}

.detail-grid dd {
  margin: 0;
  word-break: break-all;
}

.detail-grid__full {
  grid-column: 1 / -1;
}

.block {
  border-top: 1px solid var(--line);
  padding-top: 12px;
  margin-top: 12px;
}

.block__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}

.block__head h3 {
  margin: 0;
  font-size: 13px;
  font-weight: 600;
}

.task-card {
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 10px 12px;
  margin-bottom: 8px;
}

.task-card__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.task-card__id {
  color: var(--muted);
  font-size: 11px;
}

.task-card__meta {
  color: var(--muted);
  font-size: 11px;
  margin: 6px 0 8px;
}

.task-card__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.task-card__search {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 8px;
}

.verify {
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 8px 10px;
  margin-bottom: 10px;
  background: #fafbfc;
}

.verify__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.verify__time {
  color: var(--muted);
  font-size: 11px;
}

.verify__reason {
  margin-top: 6px;
  font-size: 12px;
  color: var(--text);
}

.verify__entries {
  list-style: none;
  margin: 6px 0 0;
  padding: 0;
  font-size: 11px;
  color: var(--muted);
}

.verify__entries li {
  padding: 2px 0;
  border-bottom: 1px dashed #eef0f3;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.verify__suggest {
  margin-top: 6px;
  font-size: 11px;
  color: var(--warn);
}

.timeline {
  list-style: none;
  margin: 0;
  padding: 0;
}

.timeline li {
  display: flex;
  gap: 8px;
  font-size: 12px;
  padding: 5px 0;
  border-bottom: 1px dashed #eef0f3;
}

.timeline__time {
  color: var(--muted);
  min-width: 88px;
}

.timeline__note {
  color: var(--muted);
  margin-left: auto;
}

.candidate-list {
  list-style: none;
  margin: 0 0 10px;
  padding: 0;
}

.candidate-list li {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  padding: 6px 0;
  border-bottom: 1px dashed #eef0f3;
  font-size: 12px;
}

.candidate-list__raw {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.candidate-list__meta {
  color: var(--muted);
  flex-shrink: 0;
}

.shot {
  width: 100%;
  border: 1px solid var(--line);
  border-radius: 8px;
  margin-top: 6px;
}

.btn {
  height: 30px;
  padding: 0 14px;
  border-radius: 6px;
  border: 1px solid var(--line);
  background: #fff;
  color: var(--text);
  font-size: 12px;
  cursor: pointer;
  transition: all 0.15s ease;
}

.btn:hover:not(:disabled) {
  border-color: var(--brand);
  color: var(--brand);
}

.btn:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.btn--primary {
  background: var(--brand);
  border-color: var(--brand);
  color: #fff;
}

.btn--primary:hover:not(:disabled) {
  background: #1a6a4f;
  color: #fff;
}

.btn--sm {
  height: 26px;
  padding: 0 10px;
  font-size: 12px;
}
</style>
