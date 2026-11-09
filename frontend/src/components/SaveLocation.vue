<script setup>
import { CopyDocument, FolderOpened, Loading } from '@element-plus/icons-vue'
import { ref, watch } from 'vue'

const props = defineProps({ path: String, pathError: String, pick: Function, disabled: Boolean, loading: Boolean })
const picking = defineModel('picking', { default: false })
const copied = ref('')
const error = ref('')
watch(() => props.path, () => { copied.value = '' })

async function openPicker() {
  picking.value = true
  error.value = ''
  try {
    await props.pick()
  } catch (err) {
    error.value = err.message
  } finally {
    picking.value = false
  }
}

async function copyPath() {
  try {
    await navigator.clipboard.writeText(props.path)
    copied.value = '已复制'
  } catch {
    copied.value = '可选中路径后复制'
  }
}
</script>

<template>
  <div class="save-card">
    <div class="save-row">
      <button class="icon-button folder-picker" type="button" title="选择下载文件夹" aria-label="选择下载文件夹"
        :disabled="disabled || picking" :aria-busy="picking" @click="openPicker">
        <Loading v-if="picking" class="spinning" /><FolderOpened v-else />
      </button>
      <div class="save-content">
        <code v-if="path">{{ path }}</code>
        <span v-else-if="!loading" class="quiet">{{ pathError || '请选择下载文件夹' }}</span>
        <span class="copy-feedback" aria-live="polite">{{ copied }}</span>
      </div>
      <button v-if="path" class="icon-button" type="button" title="复制路径" aria-label="复制保存路径" @click="copyPath"><CopyDocument /></button>
    </div>
    <p v-if="error" class="path-error error-text" role="alert">{{ error }}</p>
  </div>
</template>
