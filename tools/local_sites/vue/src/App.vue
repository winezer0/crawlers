<script setup>
import { onBeforeUnmount, onMounted, ref } from "vue";
import LazyPanel from "./LazyPanel.vue";

const path = ref(window.location.pathname);
const catalog = ref(null);
const clickResult = ref(null);
const interactionResult = ref(null);
const menuOpen = ref(false);
const page = ref(1);
let scrollRequested = false;

async function requestInteraction(elementID, suffix = "") {
  const target = document.getElementById(elementID);
  if (!target?.dataset.apiRoot || !target.dataset.apiName) return;
  const endpoint = `/${target.dataset.apiRoot}/${target.dataset.apiName}`;
  const response = await fetch(`${endpoint}${suffix}`);
  interactionResult.value = await response.json();
}

function nextPage() {
  page.value += 1;
  void requestInteraction("vue-page-next", `?page=${page.value}`);
}

function onScroll() {
  const root = document.scrollingElement || document.documentElement;
  if (!scrollRequested && window.scrollY + window.innerHeight >= root.scrollHeight - 20) {
    scrollRequested = true;
    void requestInteraction("vue-scroll-trigger");
  }
}

onMounted(async () => {
  window.addEventListener("scroll", onScroll, { passive: true });
  try {
    const response = await fetch("/api/vue-catalog");
    catalog.value = await response.json();
  } catch {
    catalog.value = { items: [] };
  }
});

onBeforeUnmount(() => window.removeEventListener("scroll", onScroll));

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
    <button id="vue-menu-open" type="button" aria-haspopup="true" @click="menuOpen = true">Open menu</button>
    <button v-if="menuOpen" id="vue-menu-load" type="button" data-api-root="api" data-api-name="vue-menu" @click="requestInteraction('vue-menu-load')">
      Load menu data
    </button>
    <button id="vue-page-next" type="button" data-api-root="api" data-api-name="vue-page" :disabled="page > 1" @click="nextPage">Load next page</button>
    <p>Current page: {{ page }}</p>
    <pre>{{ JSON.stringify(interactionResult) }}</pre>
    <pre>{{ JSON.stringify(clickResult) }}</pre>
    <pre>{{ JSON.stringify(catalog) }}</pre>
    <span id="vue-scroll-trigger" hidden data-api-root="api" data-api-name="vue-scroll"></span>
    <LazyPanel />
    <div aria-hidden="true" style="height: 1400px"></div>
  </main>
</template>
