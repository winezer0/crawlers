<script setup>
import { ref } from 'vue'
const site = new URLSearchParams(location.search).get('site') || import.meta.env.VITE_SITE_ID || 'framework-vue'
const result = ref('')
const todos = ref([{ id: 1, title: 'Map routes', done: false }, { id: 2, title: 'Check API methods', done: true }])
const draft = ref('')
function addTodo() { if (draft.value.trim()) { todos.value.push({ id: Date.now(), title: draft.value.trim(), done: false }); draft.value = '' } }
async function load() {
  const response = await fetch(`http://127.0.0.1:8090/api/${site}/catalog`)
  result.value = await response.text()
}
async function search(event) {
  event.preventDefault()
  const response = await fetch(`http://127.0.0.1:8090/api/${site}/search`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ query: new FormData(event.target).get('query') }) })
  result.value = await response.text()
}
</script>
<template>
  <section><h1>{{ site }} home</h1><p>A Vue single-page application with client-side routes, a route parameter, and an interaction-triggered API call.</p>
    <div v-if="site === 'app-vue-todomvc'" class="todo"><h2>TodoMVC</h2><form @submit.prevent="addTodo"><input v-model="draft" aria-label="New todo" placeholder="What needs doing?"><button>Add todo</button></form><ul><li v-for="todo in todos" :key="todo.id"><label><input v-model="todo.done" type="checkbox">{{todo.title}}</label></li></ul><p>{{todos.filter(todo => !todo.done).length}} items left</p></div>
    <a href="/items/item-01">Open item 01</a>
    <button @click="load">Load catalog</button>
    <form @submit="search"><label>Search <input name="query" value="blue"></label><button>Search API</button></form>
    <pre data-testid="api-result">{{ result }}</pre>
  </section>
</template>
