<script setup>
const site = new URLSearchParams(location.search).get('site') || import.meta.env.VITE_SITE_ID || 'framework-vue'
window.__testwebSite = site
</script>
<template>
  <header><strong>{{ site }} · Vue Router</strong><nav><RouterLink to="/">Home</RouterLink> <RouterLink to="/guide">Guide</RouterLink> <RouterLink to="/items/item-01">Item</RouterLink></nav></header>
  <main><RouterView /></main>
</template>
