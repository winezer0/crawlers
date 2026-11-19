<script setup lang="ts">
import { getNuxtCandidateDecoyFixture } from "~/utils/nuxtCandidateDecoys";

const { data: catalog } = await useFetch("/api/nuxt-catalog");
const candidateDecoyFixture = getNuxtCandidateDecoyFixture();
const clickResult = ref<unknown>(null);
const interactionResult = ref<unknown>(null);
const menuOpen = ref(false);
const page = ref(1);
let scrollRequested = false;

function readEditorToken(token: string) {
  return token;
}

function renderPreview(markup: string) {
  return markup;
}

const nonRequestExamples = [
  "accessibility.signals.chatRequestSent",
  "editor.background",
  "workbench.hover.pointer",
  "a.action-label",
  "starting-nuxt-server",
  "<span>example</span>",
];

for (const token of nonRequestExamples) {
  readEditorToken(token);
}

renderPreview("<span>rendered as text, not a route</span>");

async function triggerRuntimeRequest() {
  const button = document.querySelector<HTMLButtonElement>("#nuxt-runtime-trigger");
  if (!button?.dataset.apiRoot || !button.dataset.apiName) return;
  const endpoint = `/${button.dataset.apiRoot}/${button.dataset.apiName}`;
  clickResult.value = await $fetch(`${endpoint}?source=button`);
}

async function requestInteraction(elementID: string, suffix = "") {
  const target = document.getElementById(elementID);
  if (!target?.dataset.apiRoot || !target.dataset.apiName) return;
  const endpoint = `/${target.dataset.apiRoot}/${target.dataset.apiName}`;
  interactionResult.value = await $fetch(`${endpoint}${suffix}`);
}

function nextPage() {
  page.value += 1;
  void requestInteraction("nuxt-page-next", `?page=${page.value}`);
}

function onScroll() {
  const root = document.scrollingElement || document.documentElement;
  if (!scrollRequested && window.scrollY + window.innerHeight >= root.scrollHeight - 20) {
    scrollRequested = true;
    void requestInteraction("nuxt-scroll-trigger");
  }
}

onMounted(() => window.addEventListener("scroll", onScroll, { passive: true }));
onBeforeUnmount(() => window.removeEventListener("scroll", onScroll));
</script>

<template>
  <main>
    <h1>Local Nuxt crawl fixture</h1>
    <nav aria-label="Nuxt fixture routes">
      <NuxtLink to="/nuxt/rendering">Rendering route</NuxtLink>
      <NuxtLink to="/nuxt/client">Client route</NuxtLink>
    </nav>
    <pre id="nuxt-catalog">{{ catalog }}</pre>
    <form action="/api/nuxt-search" method="post">
      <label>Search Nuxt fixture <input name="query" value="nuxt-local" /></label>
      <input type="hidden" name="csrf" value="local-fixture-token" />
      <button type="submit">Submit Nuxt search</button>
    </form>
    <button
      id="nuxt-runtime-trigger"
      type="button"
      data-api-root="api"
      data-api-name="nuxt-click"
      @click="triggerRuntimeRequest"
    >
      Load runtime endpoint
    </button>
    <button id="nuxt-menu-open" type="button" aria-haspopup="true" @click="menuOpen = true">Open menu</button>
    <button v-if="menuOpen" id="nuxt-menu-load" type="button" data-api-root="api" data-api-name="nuxt-menu" @click="requestInteraction('nuxt-menu-load')">
      Load menu data
    </button>
    <button id="nuxt-page-next" type="button" data-api-root="api" data-api-name="nuxt-page" :disabled="page > 1" @click="nextPage">Load next page</button>
    <p>Current page: {{ page }}</p>
    <pre id="nuxt-interaction-result">{{ interactionResult }}</pre>
    <pre id="nuxt-runtime-result">{{ clickResult }}</pre>
    <span id="nuxt-scroll-trigger" hidden data-api-root="api" data-api-name="nuxt-scroll"></span>
    <output
      id="nuxt-candidate-decoy-fixture"
      :data-count="candidateDecoyFixture.count"
      :data-checksum="candidateDecoyFixture.checksum"
    >
      {{ candidateDecoyFixture.count }} offline candidate strings
    </output>
    <LazyNuxtPanel />
    <div aria-hidden="true" style="height: 1400px"></div>
  </main>
</template>
