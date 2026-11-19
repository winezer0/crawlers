<script setup lang="ts">
import { getNuxtCandidateDecoyFixture } from "~/utils/nuxtCandidateDecoys";

const { data: catalog } = await useFetch("/api/nuxt-catalog");
const candidateDecoyFixture = getNuxtCandidateDecoyFixture();
const clickResult = ref<unknown>(null);

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
    <pre id="nuxt-runtime-result">{{ clickResult }}</pre>
    <output
      id="nuxt-candidate-decoy-fixture"
      :data-count="candidateDecoyFixture.count"
      :data-checksum="candidateDecoyFixture.checksum"
    >
      {{ candidateDecoyFixture.count }} offline candidate strings
    </output>
    <LazyNuxtPanel />
  </main>
</template>
