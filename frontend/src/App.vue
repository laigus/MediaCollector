<script setup>
import { ref, nextTick } from 'vue'
import { FolderOpened, Picture, VideoCamera } from '@element-plus/icons-vue'
import DouyinView from './views/DouyinView.vue'
import WeiboView from './views/WeiboView.vue'

const tabs = [
  { id: 'douyin', name: '抖音', icon: VideoCamera },
  { id: 'weibo', name: '微博', icon: Picture },
]
const active = ref('douyin')

async function navigate(event) {
  const current = tabs.findIndex(({ id }) => id === active.value)
  const next = { ArrowLeft: (current + 1) % 2, ArrowRight: (current + 1) % 2, Home: 0, End: 1 }[event.key]
  if (next === undefined) return
  event.preventDefault()
  active.value = tabs[next].id
  await nextTick()
  document.getElementById('tab-' + active.value)?.focus()
}
</script>

<template>
  <header class="topbar">
    <div class="topbar-inner">
      <div class="brand">
        <span class="brand-mark"><FolderOpened /></span>
        <strong>MediaCollector</strong>
      </div>
      <nav class="platform-tabs" role="tablist" aria-label="下载平台" @keydown="navigate">
        <button v-for="tab in tabs" :id="'tab-' + tab.id" :key="tab.id" role="tab" type="button"
          :aria-selected="active === tab.id" :tabindex="active === tab.id ? 0 : -1"
          :aria-controls="'panel-' + tab.id" @click="active = tab.id">
          <component :is="tab.icon" />{{ tab.name }}
        </button>
      </nav>
    </div>
  </header>
  <main class="workspace">
    <section v-show="active === 'douyin'" id="panel-douyin" role="tabpanel" aria-labelledby="tab-douyin" tabindex="0">
      <DouyinView />
    </section>
    <section v-show="active === 'weibo'" id="panel-weibo" role="tabpanel" aria-labelledby="tab-weibo" tabindex="0">
      <WeiboView />
    </section>
  </main>
</template>
