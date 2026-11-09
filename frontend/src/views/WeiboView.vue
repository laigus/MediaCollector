<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ArrowDown, Download, Link } from '@element-plus/icons-vue'
import api from '../api'
import SaveLocation from '../components/SaveLocation.vue'
import PlatformLogin from '../components/PlatformLogin.vue'
import DownloadStatus from '../components/DownloadStatus.vue'
import { useDownload } from '../useDownload'

const destination = ref('')
const loggingIn = ref(false)
const configError = ref('')
const loadingConfig = ref(true)
const pickingPath = ref(false)
const form = reactive({ target_url: '', start_page: 1, end_page: 0 })
const { task, submitting, error, pollError, running, submit, dismiss } = useDownload('weibo')

async function loadConfig() {
  try {
    const data = await api.destination('weibo')
    destination.value = data.download_dir
  } catch (err) {
    configError.value = err.message
  } finally {
    loadingConfig.value = false
  }
}

async function pickPath() {
  const { download_dir } = await api.chooseDownloadFolder('weibo')
  if (download_dir) {
    destination.value = download_dir
    configError.value = ''
  }
}

function download() {
  if (!destination.value || pickingPath.value || loggingIn.value) return
  const body = { ...form, target_url: form.target_url.trim(), start_page: Number(form.start_page) || 1, end_page: Number(form.end_page) || 0 }
  submit(() => api.downloadWeibo(body))
}

function retry() {
  submit(() => api.retry(task.value.id))
}

onMounted(loadConfig)
</script>

<template>
  <SaveLocation v-model:picking="pickingPath" :path="destination" :path-error="configError"
    :pick="pickPath" :loading="loadingConfig" :disabled="loadingConfig || running || submitting || loggingIn" />

  <DownloadStatus :task="task" :poll-error="pollError" :busy="submitting || pickingPath || loggingIn" @retry="retry" @dismiss="dismiss" />

  <section class="library">
    <div class="section-heading">
      <h1>微博主页</h1>
      <PlatformLogin v-model:pending="loggingIn" platform="weibo" label="微博" :disabled="running || submitting || pickingPath" />
    </div>
    <form class="weibo-form" @submit.prevent="download">
      <label for="weibo-url">主页链接或用户 ID</label>
      <div class="url-input"><Link /><input id="weibo-url" v-model="form.target_url" placeholder="粘贴微博主页链接，或输入数字 ID" required /></div>
      <p class="quiet">如需登录或验证，在弹出的微博窗口完成操作。</p>
      <details class="form-options">
        <summary>下载范围 <ArrowDown class="disclosure-icon" /></summary>
        <div class="advanced-fields">
          <label>起始页<input v-model.number="form.start_page" type="number" min="1" required /></label>
          <label>结束页（0 为全部）<input v-model.number="form.end_page" type="number" min="0" required /></label>
        </div>
      </details>
      <button type="submit" class="button button-primary" :disabled="!form.target_url.trim() || !destination || running || submitting || pickingPath || loggingIn">
        <Download /> {{ running ? '下载中…' : submitting ? '正在读取主页…' : '下载微博原图' }}
      </button>
      <p v-if="error" class="inline-note error-text" role="alert">{{ error }}</p>
    </form>
  </section>
</template>
