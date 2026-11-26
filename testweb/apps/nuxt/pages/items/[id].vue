<script setup lang="ts">const route = useRoute(); const item = await $fetch(`http://127.0.0.1:8090/api/framework-nuxt/items/${route.params.id}`)</script>
<template><section><h1>Nuxt item {{ $route.params.id }}</h1><pre>{{item}}</pre><NuxtLink to="/">Home</NuxtLink></section></template>
