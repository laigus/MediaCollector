<template>
  <div>
    <div class="page-header">
      <h2>设置</h2>
      <p>配置各平台的认证信息和参数</p>
    </div>

    <div v-loading="loading">
      <el-card
        v-for="provider in providers"
        :key="provider.name"
        shadow="never"
        style="border-radius: 12px; max-width: 640px; margin-bottom: 20px;"
      >
        <template #header>
          <div style="display: flex; align-items: center; gap: 8px;">
            <el-tag type="primary" size="small">{{ provider.display_name }}</el-tag>
            <span style="color: #909399; font-size: 13px;">{{ provider.description }}</span>
          </div>
        </template>

        <el-form label-position="top">
          <el-form-item
            v-for="(schema, key) in provider.config_schema"
            :key="key"
            :label="schema.title || key"
          >
            <el-input
              v-if="schema.type === 'string'"
              v-model="configForms[provider.name][key]"
              :placeholder="maskedHints[provider.name]?.[key] ? ('已配置: ' + maskedHints[provider.name][key] + '（留空保持不变，输入新值覆盖）') : (schema.description || '')"
              :type="key.toLowerCase().includes('cookie') || key.toLowerCase().includes('token') ? 'textarea' : 'text'"
              :rows="3"
            />
            <div v-if="schema.description" style="font-size: 12px; color: #909399; margin-top: 4px;">
              {{ schema.description }}
            </div>
          </el-form-item>

          <el-form-item>
            <el-button
              type="primary"
              @click="handleSave(provider.name)"
              :loading="saving[provider.name]"
            >
              保存配置
            </el-button>
          </el-form-item>
        </el-form>
      </el-card>
    </div>

    <el-card shadow="never" style="border-radius: 12px; max-width: 640px;">
      <template #header>
        <span style="font-weight: 600;">系统信息</span>
      </template>
      <el-descriptions :column="1" border>
        <el-descriptions-item label="后端状态">
          <el-tag :type="backendOk ? 'success' : 'danger'" size="small">
            {{ backendOk ? '正常' : '离线' }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="版本">{{ backendVersion }}</el-descriptions-item>
      </el-descriptions>
    </el-card>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api'

const loading = ref(false)
const providers = ref([])
const configForms = reactive({})
const maskedHints = reactive({})
const saving = reactive({})
const backendOk = ref(false)
const backendVersion = ref('--')

function isMasked(val) {
  return typeof val === 'string' && val.includes('***')
}

async function fetchAll() {
  loading.value = true
  try {
    const [{ data: providerList }, healthResp] = await Promise.all([
      api.listProviders(),
      api.health().catch(() => ({ data: { status: 'error', version: '--' } })),
    ])

    providers.value = providerList
    backendOk.value = healthResp.data.status === 'ok'
    backendVersion.value = healthResp.data.version || '--'

    for (const p of providerList) {
      saving[p.name] = false
      maskedHints[p.name] = {}
      try {
        const { data } = await api.getProviderConfig(p.name)
        const rawConfig = data.config || {}
        configForms[p.name] = {}
        for (const [k, v] of Object.entries(rawConfig)) {
          if (isMasked(v)) {
            maskedHints[p.name][k] = v
            configForms[p.name][k] = ''
          } else {
            configForms[p.name][k] = v
          }
        }
      } catch {
        configForms[p.name] = {}
      }
      for (const key of Object.keys(p.config_schema)) {
        if (!(key in configForms[p.name])) {
          configForms[p.name][key] = ''
        }
      }
    }
  } catch (e) {
    console.error(e)
  } finally {
    loading.value = false
  }
}

async function handleSave(providerName) {
  saving[providerName] = true
  try {
    await api.updateProviderConfig(providerName, configForms[providerName])
    ElMessage.success('配置已保存')
  } catch (e) {
    ElMessage.error('保存失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    saving[providerName] = false
  }
}

onMounted(fetchAll)
</script>
