<script setup lang="ts">
const site = 'framework-nuxt'
const result = ref('')
async function load() { result.value = await $fetch(`http://127.0.0.1:8090/api/${site}/catalog`) .then(v => JSON.stringify(v)) }
async function search(event: Event) { event.preventDefault(); const form = event.target as HTMLFormElement; result.value = JSON.stringify(await $fetch(`http://127.0.0.1:8090/api/${site}/search`, { method: 'POST', body: { query: new FormData(form).get('query') } })) }
</script>
<template><div><header><strong>framework-nuxt · Nuxt SSR</strong><nav><NuxtLink to="/">Home</NuxtLink><NuxtLink to="/guide">Guide</NuxtLink><NuxtLink to="/items/item-01">Item 01</NuxtLink></nav></header><main><NuxtPage /><section><button @click="load">Load catalog</button><form @submit="search"><label>Search <input name="query" value="blue"></label><button>Search API</button></form><pre data-testid="api-result">{{result}}</pre></section></main></div></template>
<style>body{margin:0;background:#f0f7f7;color:#172033;font:16px system-ui}header,main{max-width:900px;margin:auto;padding:1.5rem}header,nav{display:flex;justify-content:space-between;gap:1rem}main{background:white;margin-top:1rem;padding:2rem}button,input{padding:.5rem;margin:.4rem}pre{white-space:pre-wrap}</style>
