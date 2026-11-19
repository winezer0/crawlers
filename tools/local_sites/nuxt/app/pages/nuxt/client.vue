<script setup lang="ts">
const clickResult = ref<unknown>(null);

function openRenderingRoute() {
  return navigateTo("/nuxt/rendering?via=navigateTo");
}

async function triggerRuntimeRequest() {
  const button = document.querySelector<HTMLButtonElement>("#nuxt-client-runtime-trigger");
  if (!button?.dataset.apiRoot || !button.dataset.apiName) return;
  const endpoint = `/${button.dataset.apiRoot}/${button.dataset.apiName}`;
  clickResult.value = await $fetch(`${endpoint}?source=client-button`);
}
</script>

<template>
  <main>
    <h1>Nuxt client route</h1>
    <button type="button" @click="openRenderingRoute">Open rendering route</button>
    <button
      id="nuxt-client-runtime-trigger"
      type="button"
      data-api-root="api"
      data-api-name="nuxt-click"
      @click="triggerRuntimeRequest"
    >
      Load client runtime endpoint
    </button>
    <pre>{{ clickResult }}</pre>
    <NuxtLink to="/">Home</NuxtLink>
  </main>
</template>
