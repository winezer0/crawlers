<script setup lang="ts">
const { data: catalog } = await useFetch("/api/nuxt-catalog?view=rendering");
</script>

<template>
  <main>
    <h1>Nuxt rendering route</h1>
    <NuxtLink to="/nuxt/client?via=rendering">Client route</NuxtLink>
    <pre>{{ catalog }}</pre>
  </main>
</template>
