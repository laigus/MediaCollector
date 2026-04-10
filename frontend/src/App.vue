<template>
  <div class="layout">
    <aside class="sidebar">
      <div class="sidebar-header">
        <span>Media</span>Collector
      </div>
      <nav class="sidebar-nav">
        <router-link
          v-for="item in navItems"
          :key="item.path"
          :to="item.path"
          class="nav-item"
          active-class="active"
        >
          <el-icon><component :is="item.icon" /></el-icon>
          {{ item.label }}
        </router-link>
      </nav>
      <div style="padding: 16px 24px; font-size: 12px; color: rgba(255,255,255,0.3);">
        v0.1.0
      </div>
    </aside>
    <main class="main-content">
      <router-view />
    </main>
  </div>
</template>

<script setup>
const navItems = [
  { path: '/', label: '任务面板', icon: 'Monitor' },
  { path: '/new-task', label: '新建任务', icon: 'Plus' },
  { path: '/settings', label: '设置', icon: 'Setting' },
]
</script>
