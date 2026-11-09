<script setup>
import { onMounted, onUnmounted, ref, watch } from 'vue'
import { Loading, User } from '@element-plus/icons-vue'
import api from '../api'

const props = defineProps({
  platform: String, label: String, disabled: Boolean,
  session: Object, checking: Boolean, sessionError: Boolean,
})
const pending = defineModel('pending', { default: false })
const emit = defineEmits(['logged-in', 'logged-out', 'check-session'])
const message = ref('')
const error = ref(false)
const avatarFailed = ref(false)
const account = ref(null)
const accountButton = ref(null)
const accountOpen = ref(false)
watch(() => props.session?.avatar_url, () => { avatarFailed.value = false })

function closeOutside(event) {
  if (!account.value?.contains(event.target)) accountOpen.value = false
}

function closeAccount() {
  if (!accountOpen.value) return
  accountOpen.value = false
  accountButton.value?.focus()
}

onMounted(() => document.addEventListener('pointerdown', closeOutside))
onUnmounted(() => document.removeEventListener('pointerdown', closeOutside))

async function login() {
  pending.value = true
  message.value = '在弹出的窗口中登录'
  error.value = false
  try {
    await api.login(props.platform)
    message.value = '登录成功'
    emit('logged-in')
  } catch (err) {
    message.value = err.message
    error.value = true
  } finally {
    pending.value = false
  }
}

async function logout() {
  pending.value = true
  error.value = false
  message.value = ''
  try {
    const session = await api.logoutDouyin()
    accountOpen.value = false
    emit('logged-out', session)
  } catch (err) {
    message.value = err.message
    error.value = true
  } finally {
    pending.value = false
  }
}
</script>

<template>
  <div class="platform-login">
    <span v-if="message && !session?.logged_in" class="login-message" :class="{ 'error-text': error }" aria-live="polite">{{ message }}</span>
    <div v-if="session?.logged_in" ref="account" class="account-control" @keydown.esc.stop="closeAccount">
      <button ref="accountButton" class="account-avatar" type="button" aria-label="当前抖音账号"
        :aria-expanded="accountOpen" :title="session.nickname || '当前抖音账号'" @click="accountOpen = !accountOpen">
        <img v-if="session.avatar_url && !avatarFailed" :src="session.avatar_url" alt="" referrerpolicy="no-referrer"
          @error="avatarFailed = true" />
        <User v-else />
        <span class="account-dot"></span>
      </button>
      <div v-if="accountOpen" class="account-panel">
        <strong>{{ session.nickname || '抖音账号' }}</strong>
        <span v-if="session.account_id" class="quiet">抖音号：{{ session.account_id }}</span>
        <button class="button button-muted" type="button" :disabled="disabled || pending" @click="logout">
          {{ pending ? '正在退出…' : '退出登录' }}
        </button>
        <p v-if="error" class="login-message error-text" role="alert">{{ message }}</p>
      </div>
    </div>
    <span v-else-if="checking" class="account-check" role="status" aria-label="正在检查登录状态">
      <Loading class="spinning" />
    </span>
    <button v-else-if="sessionError" class="button button-muted" type="button" @click="emit('check-session')">
      重试登录状态
    </button>
    <button v-else class="button button-muted" type="button" :disabled="disabled || pending" @click="login">
      <User />{{ pending ? '等待登录…' : '登录' + label }}
    </button>
  </div>
</template>
