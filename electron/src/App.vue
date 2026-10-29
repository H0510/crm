<template>
  <div class="shell">
    <nav class="nav">
      <div class="nav__brand">
        <span class="nav__mark">微</span>
        <div>
          <div class="nav__title">微信获客</div>
          <div class="nav__sub">桌面工作台</div>
        </div>
      </div>

      <button
        v-for="item in tabs"
        :key="item.key"
        class="nav__item"
        :class="{ 'is-active': active === item.key }"
        @click="active = item.key"
      >
        <svg class="nav__icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6">
          <path :d="item.icon" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
        <span class="nav__label">
          {{ item.label }}
          <small>{{ item.desc }}</small>
        </span>
      </button>

      <div class="nav__foot">
        <span>本地服务</span>
        <span class="nav__addr">127.0.0.1:8756</span>
      </div>
    </nav>

    <main class="shell__body">
      <KeepAlive>
        <component :is="currentView" :api-base="apiBase" />
      </KeepAlive>
    </main>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import WorkbenchView from './views/WorkbenchView.vue'
import CollectionView from './views/CollectionView.vue'
import LibraryView from './views/LibraryView.vue'
import QuickAddView from './views/QuickAddView.vue'

// 本地 python 服务地址，桌面端和浏览器里跑都用它
const apiBase = 'http://127.0.0.1:8756'

const tabs = [
  {
    key: 'workbench',
    label: '好友申请',
    desc: '匹配与任务流转',
    view: WorkbenchView,
    icon: 'M9 11a3.5 3.5 0 100-7 3.5 3.5 0 000 7zM3 20c0-2.8 2.7-5 6-5s6 2.2 6 5M18 8v6M21 11h-6',
  },
  {
    key: 'collect',
    label: '通讯录采集',
    desc: '遍历读取微信号',
    view: CollectionView,
    icon: 'M12 4v9m0 0l-3-3m3 3l3-3M5 18v2h14v-2',
  },
  {
    key: 'quick',
    label: '直接添加',
    desc: '输微信号或手机号',
    view: QuickAddView,
    icon: 'M12 5v14M5 12h14',
  },
  {
    key: 'library',
    label: '联系人库',
    desc: '已采集联系人',
    view: LibraryView,
    icon: 'M5 7h14M5 12h14M5 17h9',
  },
]

const active = ref('collect')
const currentView = computed(() => tabs.find((item) => item.key === active.value)?.view)
</script>

<style scoped>
.shell {
  display: flex;
  height: 100%;
  background: var(--bg);
}

.nav {
  width: 208px;
  flex: none;
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 16px 12px;
  box-sizing: border-box;
  background: var(--panel);
  border-right: 1px solid var(--line);
}

.nav__brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 4px 8px 16px;
}

.nav__mark {
  display: grid;
  place-items: center;
  width: 30px;
  height: 30px;
  border-radius: 8px;
  background: var(--brand-soft);
  color: var(--brand);
  font-weight: 600;
}

.nav__title {
  font-size: 14px;
  font-weight: 600;
}

.nav__sub {
  color: var(--muted);
  font-size: 11px;
}

.nav__item {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 9px 10px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: var(--text);
  text-align: left;
  cursor: pointer;
}

.nav__item:hover {
  background: #f4f6f9;
}

.nav__item.is-active {
  background: var(--brand-soft);
  color: var(--brand);
}

.nav__icon {
  flex: none;
  width: 18px;
  height: 18px;
  color: var(--muted);
}

.nav__item.is-active .nav__icon {
  color: var(--brand);
}

.nav__label {
  display: flex;
  flex-direction: column;
  font-size: 13px;
}

.nav__label small {
  color: var(--muted);
  font-size: 11px;
}

.nav__item.is-active .nav__label small {
  color: var(--brand);
  opacity: 0.75;
}

.nav__foot {
  margin-top: auto;
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 10px 8px 0;
  border-top: 1px solid var(--line);
  color: var(--muted);
  font-size: 11px;
}

.nav__addr {
  font-family: Consolas, monospace;
}

.shell__body {
  flex: 1;
  min-width: 0;
  overflow: hidden;
}
</style>
