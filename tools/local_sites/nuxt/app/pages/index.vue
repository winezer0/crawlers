<script setup lang="ts">
import { getNuxtCandidateDecoyFixture } from "~/utils/nuxtCandidateDecoys";

const { data: catalog } = await useFetch("/api/nuxt-catalog");
const candidateDecoyFixture = getNuxtCandidateDecoyFixture();

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
</script>

<template>
  <main>
    <h1>Local Nuxt crawl fixture</h1>
    <nav aria-label="Nuxt fixture routes">
      <NuxtLink to="/nuxt/rendering">Rendering route</NuxtLink>
      <NuxtLink to="/nuxt/client">Client route</NuxtLink>
    </nav>
    <pre id="nuxt-catalog">{{ catalog }}</pre>
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
