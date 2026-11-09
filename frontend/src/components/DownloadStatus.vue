<script setup>
import { computed, ref, watch } from 'vue'
import { CircleCheck, Close, Download, Warning } from '@element-plus/icons-vue'
import api from '../api'

const props = defineProps({ task: Object, pollError: String, busy: Boolean })
const emit = defineEmits(['retry', 'dismiss'])
const failures = ref(null)
const detailsError = ref('')
const detailsLoading = ref(false)
const running = computed(() => props.task?.status === 'running')
const warning = computed(() => props.task?.status === 'failed' || props.task?.failed_count > 0)
const processed = computed(() => (props.task?.downloaded_count || 0) + (props.task?.failed_count || 0))
watch(() => [props.task?.id, props.task?.status], () => {
  failures.value = null
  detailsError.value = ''
})

async function toggleFailures() {
  if (failures.value) {
    failures.value = null
    return
  }
  const id = props.task.id
  detailsLoading.value = true
  detailsError.value = ''
  try {
    const data = await api.failed(id)
    if (id === props.task?.id && !running.value) failures.value = data.files
  } catch (err) {
    detailsError.value = err.message
  } finally {
    detailsLoading.value = false
  }
}
</script>

<template>
  <div v-if="task || pollError" class="download-status">
    <div v-if="task" class="status-main">
      <span class="status-icon" :class="{ warning: warning && !running }">
        <Download v-if="running" /><Warning v-else-if="warning" /><CircleCheck v-else />
      </span>
      <div class="status-copy">
        <strong>{{ task.target_name }} · {{ running ? '下载中' : warning ? '有下载失败' : '下载完成' }}</strong>
        <p aria-live="polite">{{ task.progress_message }}</p>
        <progress v-if="running" :value="processed < task.total_count ? processed : undefined"
          :max="task.total_count || 1" aria-label="下载进度"></progress>
        <code v-if="task.save_dir">{{ task.save_dir }}</code>
      </div>
      <div v-if="!running" class="status-actions">
        <button v-if="task.failed_count" class="button button-muted" type="button" :disabled="detailsLoading" @click="toggleFailures">{{ failures ? '收起详情' : '失败详情' }}</button>
        <button v-if="task.failed_count" class="button button-muted" type="button" :disabled="busy" @click="emit('retry')">重试失败项</button>
        <button class="icon-button" type="button" aria-label="关闭下载状态" @click="emit('dismiss')"><Close /></button>
      </div>
    </div>
    <p v-if="pollError || detailsError" class="error-text" aria-live="polite">{{ pollError || detailsError }}</p>
    <ul v-if="failures?.length" class="failure-list">
      <li v-for="file in failures" :key="file.source_id || file.filename"><strong>{{ file.filename }}</strong> · {{ file.error }}</li>
    </ul>
    <p v-else-if="failures" class="quiet">没有可显示的失败详情。</p>
  </div>
</template>
