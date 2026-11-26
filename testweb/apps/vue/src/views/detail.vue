<script setup>
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
const route = useRoute()
const item = ref(null)
onMounted(async () => { const site = window.__testwebSite || 'framework-vue'; item.value = await (await fetch(`http://127.0.0.1:8090/api/${site}/items/${route.params.id}`)).json() })
</script>
<template><section><h1>Item {{ route.params.id }}</h1><pre>{{ item }}</pre><RouterLink to="/">Back home</RouterLink></section></template>
