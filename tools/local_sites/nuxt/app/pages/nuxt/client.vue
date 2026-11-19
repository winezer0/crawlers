<script setup lang="ts">
function openRenderingRoute() {
  return navigateTo("/nuxt/rendering?via=navigateTo");
}
</script>

<template>
  <main>
    <h1>Nuxt client route</h1>
    <button type="button" @click="openRenderingRoute">Open rendering route</button>
    <NuxtLink to="/">Home</NuxtLink>
  </main>
</template>
