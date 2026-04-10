<template>
  <div>
    <div class="page-header">
      <h2>任务面板</h2>
      <p>管理和监控所有下载任务</p>
    </div>

    <div class="stat-cards">
      <div class="stat-card">
        <div class="label">总任务数</div>
        <div class="value">{{ tasks.length }}</div>
      </div>
      <div class="stat-card">
        <div class="label">运行中</div>
        <div class="value accent">{{ runningCount }}</div>
      </div>
      <div class="stat-card">
        <div class="label">已完成</div>
        <div class="value success">{{ completedCount }}</div>
      </div>
      <div class="stat-card">
        <div class="label">失败</div>
        <div class="value danger">{{ failedCount }}</div>
      </div>
    </div>

    <el-card shadow="never" style="border-radius: 12px;">
      <template #header>
        <div style="display: flex; justify-content: space-between; align-items: center;">
          <span style="font-weight: 600;">任务列表</span>
          <el-button type="primary" @click="$router.push('/new-task')" :icon="Plus">
            新建任务
          </el-button>
        </div>
      </template>

      <el-table :data="tasks" style="width: 100%;" v-loading="loading" empty-text="暂无任务，点击右上角新建">
        <el-table-column prop="id" label="ID" width="60" />
        <el-table-column prop="provider" label="来源" width="80">
          <template #default="{ row }">
            <el-tag size="small" type="info">{{ row.provider }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="target_name" label="目标" min-width="140" show-overflow-tooltip />
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)" size="small">
              {{ statusLabel(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="进度" min-width="240">
          <template #default="{ row }">
            <div v-if="row.total_count > 0" style="display: flex; align-items: center; gap: 8px; margin-bottom: 4px;">
              <el-progress
                :percentage="Math.round((row.downloaded_count / row.total_count) * 100)"
                :stroke-width="8"
                style="flex: 1;"
              />
              <span style="font-size: 12px; color: #909399; white-space: nowrap;">
                {{ row.downloaded_count }}/{{ row.total_count }}
              </span>
            </div>
            <div v-if="row.progress_message" style="font-size: 12px; color: #606266; line-height: 1.4;">
              {{ row.progress_message }}
            </div>
            <span v-else-if="!row.total_count" style="color: #c0c4cc; font-size: 12px;">--</span>
          </template>
        </el-table-column>
        <el-table-column label="页数" width="100">
          <template #default="{ row }">
            <span style="font-size: 12px; color: #909399;">
              {{ row.start_page || 1 }} - {{ row.end_page || '∞' }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="200" fixed="right">
          <template #default="{ row }">
            <el-button
              v-if="row.status === 'pending' || row.status === 'failed'"
              type="primary"
              size="small"
              link
              @click="handleStart(row.id)"
            >
              <el-icon><VideoPlay /></el-icon> 开始
            </el-button>
            <el-button
              type="info"
              size="small"
              link
              @click="handleRefresh(row.id)"
            >
              <el-icon><Refresh /></el-icon> 刷新
            </el-button>
            <el-popconfirm title="确定删除该任务？" @confirm="handleDelete(row.id)">
              <template #reference>
                <el-button type="danger" size="small" link>
                  <el-icon><Delete /></el-icon> 删除
                </el-button>
              </template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { Plus, VideoPlay, Refresh, Delete } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import api from '../api'

const tasks = ref([])
const loading = ref(false)
let pollTimer = null

const runningCount = computed(() => tasks.value.filter(t => t.status === 'running').length)
const completedCount = computed(() => tasks.value.filter(t => t.status === 'completed').length)
const failedCount = computed(() => tasks.value.filter(t => t.status === 'failed').length)

const statusType = (s) => ({
  pending: 'info',
  running: '',
  paused: 'warning',
  completed: 'success',
  failed: 'danger',
}[s] || 'info')

const statusLabel = (s) => ({
  pending: '等待中',
  running: '运行中',
  paused: '已暂停',
  completed: '已完成',
  failed: '失败',
}[s] || s)

function startPolling() {
  if (pollTimer) clearInterval(pollTimer)
  const hasRunning = tasks.value.some(t => t.status === 'running')
  pollTimer = setInterval(fetchTasks, hasRunning ? 2000 : 5000)
}

async function fetchTasks() {
  try {
    const { data } = await api.listTasks()
    tasks.value = data
    startPolling()
  } catch (e) {
    console.error('Failed to fetch tasks', e)
  }
}

async function handleStart(id) {
  try {
    await api.startTask(id)
    ElMessage.success('任务已启动')
    await fetchTasks()
  } catch (e) {
    ElMessage.error('启动失败: ' + (e.response?.data?.detail || e.message))
  }
}

async function handleRefresh(id) {
  try {
    const { data } = await api.getTask(id)
    const idx = tasks.value.findIndex(t => t.id === id)
    if (idx >= 0) tasks.value[idx] = data
  } catch (e) {
    console.error(e)
  }
}

async function handleDelete(id) {
  try {
    await api.deleteTask(id)
    ElMessage.success('已删除')
    await fetchTasks()
  } catch (e) {
    ElMessage.error('删除失败')
  }
}

onMounted(async () => {
  loading.value = true
  await fetchTasks()
  loading.value = false
  startPolling()
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
})
</script>
