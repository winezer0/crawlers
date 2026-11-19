<script setup>
import { onMounted, ref } from "vue";
import LazyPanel from "./LazyPanel.vue";

const path = ref(window.location.pathname);
const catalog = ref(null);
const clickResult = ref(null);

onMounted(async () => {
  try {
    const response = await fetch("/api/vue-catalog");
    catalog.value = await response.json();
  } catch {
    catalog.value = { items: [] };
  }
});

function navigateToGuide() {
  window.history.pushState({}, "", "/vue/guide");
  path.value = "/vue/guide";
}

async function triggerRuntimeRequest(event) {
  const button = event.currentTarget;
  if (!button.dataset.apiRoot || !button.dataset.apiName) return;
  const endpoint = `/${[button.dataset.apiRoot, button.dataset.apiName].join("/")}`;
  const response = await fetch(`${endpoint}?source=button`);
  clickResult.value = await response.json();
}
</script>

<template>
  <main>
    <h1>Local Vue fixture</h1>
    <p>Current client route: {{ path }}</p>
    <button type="button" @click="navigateToGuide">Open guide</button>
    <button
      id="vue-runtime-trigger"
      type="button"
      data-api-root="api"
      data-api-name="vue-click"
      @click="triggerRuntimeRequest"
    >
      Load runtime endpoint
    </button>
    <pre>{{ JSON.stringify(clickResult) }}</pre>
    <pre>{{ JSON.stringify(catalog) }}</pre>
    <LazyPanel />
  </main>
</template>
