<template>
  <div>
    <div class="page-header">
      <h2>新建下载任务</h2>
      <p>选择平台并输入目标地址，开始批量下载</p>
    </div>

    <el-card shadow="never" style="border-radius: 12px; max-width: 640px;">
      <el-form
        ref="formRef"
        :model="form"
        :rules="rules"
        label-width="100px"
        label-position="top"
        @submit.prevent="handleSubmit"
      >
        <el-form-item label="下载来源" prop="provider">
          <el-select v-model="form.provider" placeholder="选择平台" style="width: 100%;">
            <el-option
              v-for="p in providers"
              :key="p.name"
              :label="p.display_name"
              :value="p.name"
            >
              <span>{{ p.display_name }}</span>
              <span style="color: #909399; font-size: 12px; margin-left: 8px;">{{ p.description }}</span>
            </el-option>
          </el-select>
        </el-form-item>

        <el-form-item label="目标地址" prop="target_url">
          <el-input
            v-if="form.provider !== 'douyin'"
            v-model="form.target_url"
            placeholder="输入用户主页 URL 或 ID，例如 https://weibo.com/u/6279793937"
          />
          <div v-else style="width: 100%;">
            <el-select
              v-model="form.target_url"
              placeholder="选择下载来源"
              style="width: 100%;"
              :loading="loadingCollects"
            >
              <el-option label="我的收藏（全部）" value="favorite" />
              <el-option label="我的点赞" value="like" />
              <el-option-group v-if="collectsFolders.length > 0" label="收藏夹">
                <el-option
                  v-for="f in collectsFolders"
                  :key="f.id"
                  :label="`${f.name}（${f.count}个）`"
                  :value="`collects_${f.id}|${f.name}`"
                />
              </el-option-group>
            </el-select>
            <el-button
              v-if="collectsFolders.length === 0"
              type="primary"
              link
              size="small"
              style="margin-top: 4px;"
              :loading="loadingCollects"
              @click="fetchCollects"
            >
              {{ loadingCollects ? '正在获取收藏夹列表（需要登录）...' : '加载收藏夹列表' }}
            </el-button>
          </div>
        </el-form-item>

        <el-form-item label="保存目录">
          <el-input
            v-model="form.save_dir"
            placeholder="留空则自动生成（默认下载目录/平台/用户名）"
          />
        </el-form-item>

        <el-form-item label="页数范围">
          <div style="display: flex; align-items: center; gap: 8px;">
            <el-input-number
              v-model="form.start_page"
              :min="1"
              :max="9999"
              controls-position="right"
              placeholder="起始页"
            />
            <span style="color: #909399;">至</span>
            <el-input-number
              v-model="form.end_page"
              :min="0"
              :max="9999"
              controls-position="right"
              placeholder="结束页"
            />
            <span style="color: #909399; font-size: 13px; white-space: nowrap;">结束页 0 = 不限制</span>
          </div>
        </el-form-item>

        <el-form-item style="margin-top: 32px;">
          <el-button type="primary" @click="handleSubmit" :loading="submitting" size="large">
            <el-icon><Plus /></el-icon>
            创建任务
          </el-button>
          <el-button @click="handleSubmitAndStart" :loading="submitting" size="large">
            <el-icon><VideoPlay /></el-icon>
            创建并立即开始
          </el-button>
        </el-form-item>
      </el-form>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { Plus, VideoPlay } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import api from '../api'

const router = useRouter()
const formRef = ref()
const submitting = ref(false)
const providers = ref([])
const collectsFolders = ref([])
const loadingCollects = ref(false)

const form = ref({
  provider: 'weibo',
  target_url: '',
  save_dir: '',
  start_page: 1,
  end_page: 0,
})

const rules = {
  provider: [{ required: true, message: '请选择平台', trigger: 'change' }],
  target_url: [{ required: true, message: '请输入目标地址', trigger: 'blur' }],
}

async function fetchProviders() {
  try {
    const { data } = await api.listProviders()
    providers.value = data
    if (data.length > 0 && !form.value.provider) {
      form.value.provider = data[0].name
    }
  } catch (e) {
    console.error(e)
  }
}

async function fetchCollects() {
  loadingCollects.value = true
  try {
    const { data } = await api.getCollectsList('douyin')
    collectsFolders.value = data.folders || []
    if (collectsFolders.value.length === 0) {
      ElMessage.warning('未获取到收藏夹，可能需要先登录抖音')
    } else {
      ElMessage.success(`获取到 ${collectsFolders.value.length} 个收藏夹`)
    }
  } catch (e) {
    ElMessage.error('获取收藏夹失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    loadingCollects.value = false
  }
}

async function handleSubmit(andStart = false) {
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return

  submitting.value = true
  try {
    const { data: task } = await api.createTask(form.value)
    ElMessage.success(`任务 #${task.id} 创建成功`)

    if (andStart === true) {
      await api.startTask(task.id)
      ElMessage.success('任务已启动')
    }

    router.push('/')
  } catch (e) {
    ElMessage.error('创建失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    submitting.value = false
  }
}

function handleSubmitAndStart() {
  handleSubmit(true)
}

onMounted(fetchProviders)
</script>
