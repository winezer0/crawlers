<script lang="ts">let { children } = $props()</script>
<svelte:head><title>Testweb SvelteKit</title></svelte:head>
<header><strong>framework-sveltekit · SvelteKit</strong><nav><a href="/">Home</a><a href="/guide">Guide</a><a href="/items/item-01">Item 01</a></nav></header><main>{@render children()}</main>
<style> :global(body){margin:0;background:#fff7ee;color:#25201a;font:16px system-ui}header,main{max-width:900px;margin:auto;padding:1.5rem}header,nav{display:flex;justify-content:space-between;gap:1rem}main{background:white;margin-top:1rem;padding:2rem}button,input{padding:.55rem;margin:.4rem}pre{white-space:pre-wrap}</style>
