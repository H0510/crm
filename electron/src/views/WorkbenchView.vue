<template>
  <WeChatManager :api-base="apiBase" mode="workbench" class="workbench" />
</template>

<script setup>
import WeChatManager from '../pages/WeChatManager.vue'

defineProps({
  apiBase: { type: String, default: 'http://127.0.0.1:8756' },
})
</script>

<style scoped>
.workbench {
  height: 100%;
}
</style>
