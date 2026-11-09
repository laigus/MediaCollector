<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import {
  ArrowLeft, ArrowRight, Collection, Download, Folder, FolderOpened, Picture,
  RefreshRight, Search, Star, VideoCamera,
} from '@element-plus/icons-vue'
import api from '../api'
import SaveLocation from '../components/SaveLocation.vue'
import PlatformLogin from '../components/PlatformLogin.vue'
import DownloadStatus from '../components/DownloadStatus.vue'
import { useDownload } from '../useDownload'

const defaultSources = [
  { key: 'favorite', name: '全部收藏', description: '查看收藏过的所有作品', icon: Star, color: 'violet' },
  { key: 'like', name: '我的点赞', description: '查看点过赞的视频与图集', icon: Collection, color: 'rose' },
]
const filters = [
  { key: 'all', label: '全部' }, { key: 'video', label: '视频' }, { key: 'image', label: '图集' },
]
const pageSize = 24
const folderCacheKey = 'media-collector.douyin-folders'
const platformPath = ref('')
const sourcePath = ref('')
const loggingIn = ref(false)
const session = ref(null)
const checkingSession = ref(true)
const sessionError = ref(false)
const configError = ref('')
const loadingConfig = ref(true)
const sourcePathError = ref('')
const loadingSourcePath = ref(false)
const folders = ref(null)
const foldersLoading = ref(false)
const foldersError = ref('')
const source = ref(null)
const result = ref(null)
const worksLoading = ref(false)
const worksError = ref('')
const pickingPath = ref(false)
const selected = ref(new Set())
const query = ref('')
const filter = ref('all')
const limit = ref(pageSize)
const statusArea = ref(null)
const { task, submitting, error: downloadError, pollError, running, submit, dismiss } = useDownload('douyin')
const destination = computed(() => source.value ? sourcePath.value : platformPath.value)
const pathLoading = computed(() => source.value ? loadingSourcePath.value : loadingConfig.value)
let activeRead
let pathRequest = 0
let sessionRequest = 0

const filtered = computed(() => {
  const needle = query.value.trim().toLowerCase()
  return (result.value?.works || []).filter((work) => (
    (filter.value === 'all' || filter.value === work.kind)
    && (!needle || (work.title + ' ' + work.author).toLowerCase().includes(needle))
  ))
})
const visible = computed(() => filtered.value.slice(0, limit.value))
const visibleSelected = computed(() => visible.value.length > 0 && visible.value.every((work) => selected.value.has(work.id)))
const fileCount = computed(() => (result.value?.works || []).reduce((sum, work) => sum + (selected.value.has(work.id) ? work.file_count : 0), 0))

async function loadConfig() {
  try {
    const data = await api.destination('douyin')
    platformPath.value = data.download_dir
  } catch (err) {
    configError.value = err.message
  } finally {
    loadingConfig.value = false
  }
}

async function loadSourcePath(key) {
  const id = ++pathRequest
  sourcePath.value = ''
  sourcePathError.value = ''
  loadingSourcePath.value = true
  try {
    const data = await api.destination('douyin', key)
    if (id === pathRequest) sourcePath.value = data.download_dir
  } catch (err) {
    if (id === pathRequest) sourcePathError.value = err.message
  } finally {
    if (id === pathRequest) loadingSourcePath.value = false
  }
}

async function loadSession() {
  const id = ++sessionRequest
  checkingSession.value = true
  sessionError.value = false
  try {
    const account = await api.douyinSession()
    if (id === sessionRequest) session.value = account
  } catch {
    if (id === sessionRequest) sessionError.value = true
  } finally {
    if (id === sessionRequest) checkingSession.value = false
  }
}

async function pickPath() {
  const key = source.value?.key
  const { download_dir } = await api.chooseDownloadFolder('douyin', key)
  if (!download_dir) return
  if (key) {
    sourcePath.value = download_dir
    sourcePathError.value = ''
  } else {
    platformPath.value = download_dir
    configError.value = ''
  }
}

async function loadFolders() {
  foldersLoading.value = true
  foldersError.value = ''
  try {
    const data = await api.folders()
    setFolders(data.folders)
    localStorage.setItem(folderCacheKey, JSON.stringify(data.folders))
  } catch (err) {
    foldersError.value = err.message
  } finally {
    foldersLoading.value = false
  }
}

function setFolders(items) {
  folders.value = items.map((item) => ({
    key: 'collects_' + item.id + '|' + item.name,
    name: item.name,
    description: item.count == null ? '收藏夹' : item.count + ' 个作品',
  }))
}

async function openSource(item, refresh = false) {
  activeRead?.abort()
  const read = new AbortController()
  activeRead = read
  const sameSource = source.value?.key === item.key
  source.value = item
  if (!sameSource) {
    loadSourcePath(item.key)
    selected.value = new Set()
    result.value = null
    query.value = ''
    filter.value = 'all'
    limit.value = pageSize
  }
  worksLoading.value = true
  worksError.value = ''
  downloadError.value = ''
  try {
    const data = await api.works(item.key, refresh, read.signal)
    if (activeRead !== read) return
    const available = new Set(data.works.map((work) => work.id))
    selected.value = new Set([...selected.value].filter((id) => available.has(id)))
    result.value = data
  } catch (err) {
    if (activeRead === read && !read.signal.aborted) worksError.value = err.message
  } finally {
    if (activeRead === read) {
      worksLoading.value = false
      activeRead = null
    }
  }
}

function back() {
  activeRead?.abort()
  activeRead = null
  worksLoading.value = false
  ++pathRequest
  loadingSourcePath.value = false
  source.value = null
  result.value = null
  selected.value = new Set()
  downloadError.value = ''
}

function clearLibrary() {
  localStorage.removeItem(folderCacheKey)
  folders.value = null
  foldersError.value = ''
  back()
}

function onLoggedIn() {
  clearLibrary()
  loadSession()
}

function onLoggedOut(account) {
  ++sessionRequest
  checkingSession.value = false
  sessionError.value = false
  session.value = account
  clearLibrary()
  dismiss()
}

function toggle(id) {
  if (selected.value.has(id)) selected.value.delete(id)
  else selected.value.add(id)
}

function toggleVisible() {
  const remove = visibleSelected.value
  for (const work of visible.value) {
    if (remove) selected.value.delete(work.id)
    else selected.value.add(work.id)
  }
}

function duration(seconds) {
  return Math.floor(seconds / 60) + ':' + String(seconds % 60).padStart(2, '0')
}

async function download() {
  if (!destination.value || pickingPath.value) return
  const payload = { source: source.value.key, work_ids: [...selected.value] }
  if (await submit(() => api.downloadDouyin(payload))) selected.value.clear()
}

function retry() {
  submit(() => api.retry(task.value.id))
}

onMounted(() => {
  try {
    const cached = JSON.parse(localStorage.getItem(folderCacheKey))
    if (Array.isArray(cached)) setFolders(cached)
  } catch {
    localStorage.removeItem(folderCacheKey)
  }
  loadConfig()
  loadSession()
})
onUnmounted(() => activeRead?.abort())
</script>

<template>
  <SaveLocation v-model:picking="pickingPath" :path="destination"
    :path-error="source ? sourcePathError : configError" :pick="pickPath" :loading="pathLoading"
    :disabled="pathLoading || running || submitting || worksLoading || foldersLoading || loggingIn" />

  <div ref="statusArea" class="status-area">
    <DownloadStatus :task="task" :poll-error="pollError" :busy="submitting || pickingPath || loggingIn" @retry="retry" @dismiss="dismiss" />
    <p v-if="!source && downloadError" class="inline-note error-text" role="alert">{{ downloadError }}</p>
  </div>

  <section v-if="!source" class="library">
    <div class="section-heading">
      <h1>我的收藏</h1>
      <PlatformLogin v-model:pending="loggingIn" platform="douyin" label="抖音"
        :session="session" :checking="checkingSession" :session-error="sessionError"
        :disabled="foldersLoading || running || submitting || pickingPath"
        @logged-in="onLoggedIn" @logged-out="onLoggedOut" @check-session="loadSession" />
    </div>
    <div class="source-grid">
      <button v-for="item in defaultSources" :key="item.key" type="button" class="source-card"
        :class="item.color" :disabled="foldersLoading || pickingPath || loggingIn" @click="openSource(item)">
        <span class="source-icon"><component :is="item.icon" /></span>
        <span class="source-text"><strong>{{ item.name }}</strong><small>{{ item.description }}</small></span>
        <ArrowRight class="source-arrow" />
      </button>
    </div>

    <div class="folder-section">
      <div class="section-heading">
        <div class="folder-heading"><FolderOpened /><h2>收藏夹</h2><span v-if="folders" class="count-tag">{{ folders.length }}</span></div>
        <button type="button" class="button button-muted" :disabled="foldersLoading || pickingPath || loggingIn" @click="loadFolders">
          <RefreshRight :class="{ spinning: foldersLoading }" />
          {{ foldersLoading ? '正在读取…' : folders ? '刷新收藏夹' : '读取收藏夹' }}
        </button>
      </div>
      <p v-if="foldersError" class="inline-note error-text" role="alert">{{ foldersError }}</p>
      <div v-if="folders?.length" class="source-grid">
        <button v-for="item in folders" :key="item.key" type="button" class="source-card folder-card"
          :disabled="foldersLoading || pickingPath || loggingIn" @click="openSource(item)">
          <span class="source-icon"><Folder /></span>
          <span class="source-text"><strong>{{ item.name }}</strong><small>{{ item.description }}</small></span>
          <ArrowRight class="source-arrow" />
        </button>
      </div>
      <div v-else class="empty-card">
        <span class="empty-icon"><FolderOpened /></span>
        <div>
          <strong>{{ foldersLoading ? '正在同步收藏夹' : folders ? '还没有自建收藏夹' : '打开你在抖音创建的收藏夹' }}</strong>
          <p>{{ foldersLoading ? '若弹出抖音窗口，请扫码登录后等待同步。' : folders ? '也可以从上方浏览全部收藏或点赞。' : '点击「读取收藏夹」。首次使用时，在弹出的抖音窗口扫码登录。' }}</p>
        </div>
      </div>
    </div>
  </section>

  <section v-else class="library works-library">
    <div class="breadcrumbs">
      <button type="button" :disabled="submitting || pickingPath" @click="back">
        <ArrowLeft /> {{ worksLoading ? '停止读取并返回' : '返回收藏' }}
      </button>
      <span>/</span><span>{{ source.name }}</span>
    </div>
    <div class="section-heading">
      <h1>{{ source.name }}</h1>
      <button type="button" class="button button-muted" :disabled="worksLoading || submitting || pickingPath" @click="openSource(source, true)">
        <RefreshRight :class="{ spinning: worksLoading }" /> {{ worksLoading ? '正在读取…' : '刷新作品' }}
      </button>
    </div>
    <p v-if="worksError" class="inline-note error-text" role="alert">{{ worksError }}</p>
    <p v-if="result?.partial" class="inline-note">抖音还有部分作品未返回。当前显示已读取的作品，可刷新后查看。</p>
    <div v-if="worksLoading && !result" class="empty-card" aria-live="polite">
      <span class="empty-icon"><RefreshRight class="spinning" /></span>
      <div><strong>正在读取作品</strong><p>若弹出抖音窗口，请扫码登录后等待同步。</p></div>
    </div>
    <template v-else-if="result">
      <div class="work-toolbar">
        <div class="filter-group" role="group" aria-label="作品类型">
          <button v-for="item in filters" :key="item.key" type="button" :aria-pressed="filter === item.key"
            @click="filter = item.key; limit = pageSize">{{ item.label }}</button>
        </div>
        <label class="search-field"><Search /><input v-model="query" type="search" placeholder="搜索标题或作者" aria-label="搜索标题或作者" @input="limit = pageSize" /></label>
        <span class="work-count">{{ filtered.length }} 个作品</span>
      </div>
      <div v-if="visible.length" class="work-grid">
        <label v-for="work in visible" :key="work.id" class="work-card" :class="{ selected: selected.has(work.id) }">
          <div class="work-cover">
            <component :is="work.kind === 'video' ? VideoCamera : Picture" class="cover-placeholder" />
            <img v-if="work.cover_url" :src="work.cover_url" alt="" loading="lazy" referrerpolicy="no-referrer"
              @error="$event.currentTarget.hidden = true" />
            <span class="work-kind">
              {{ work.kind === 'video' ? (work.duration_seconds ? duration(work.duration_seconds) : '视频') : work.file_count + ' 张' }}
            </span>
            <input type="checkbox" :checked="selected.has(work.id)" :aria-label="'选择 ' + work.title" @change="toggle(work.id)" />
          </div>
          <div class="work-meta"><strong :title="work.title">{{ work.title }}</strong><span>{{ work.author }}</span></div>
        </label>
      </div>
      <div v-else class="empty-card">
        <span class="empty-icon"><Picture /></span>
        <div><strong>{{ result.works.length ? '没有匹配的作品' : '这里还没有可下载的作品' }}</strong>
          <p>{{ result.works.length ? '试试其他关键词或作品类型。' : '返回收藏，打开其他来源。' }}</p></div>
      </div>
      <div v-if="filtered.length > limit" class="more-row">
        <button class="button button-muted" type="button" @click="limit += pageSize">显示更多作品</button>
      </div>
      <div v-if="result.works.length" class="selection-bar">
        <div class="selection-info">
          <strong>已选 {{ selected.size }} 项</strong>
          <span v-if="selected.size">共 {{ fileCount }} 个文件</span>
          <button v-if="visible.length" class="text-button" type="button" @click="toggleVisible">{{ visibleSelected ? '取消当前页' : '全选当前页' }}</button>
          <button v-if="selected.size" class="text-button" type="button" @click="selected.clear()">清空</button>
        </div>
        <button v-if="running" class="button button-muted" type="button" @click="statusArea.scrollIntoView({ block: 'center', behavior: 'smooth' })">查看进度</button>
        <button v-else class="button button-primary" type="button" :disabled="!destination || !selected.size || submitting || worksLoading || pickingPath" @click="download">
          <Download />{{ submitting ? '正在提交…' : '下载选中作品' }}
        </button>
      </div>
    </template>
    <p v-if="downloadError" class="inline-note error-text" role="alert">{{ downloadError }}</p>
  </section>
</template>
