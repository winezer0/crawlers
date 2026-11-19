<script>
  import { onMount } from "svelte";

  let catalog = null;
  let result = null;
  let menuOpen = false;
  let page = 1;

  async function requestData(elementID, suffix = "") {
    const target = document.getElementById(elementID);
    if (!target?.dataset.apiRoot || !target.dataset.apiName) return;
    const endpoint = `/${target.dataset.apiRoot}/${target.dataset.apiName}`;
    const response = await fetch(`${endpoint}${suffix}`);
    result = await response.json();
  }

  function nextPage() {
    page += 1;
    void requestData("svelte-page-next", `?page=${page}`);
  }

  onMount(() => {
    void fetch("/api/svelte-catalog")
      .then((response) => response.json())
      .then((value) => (catalog = value));
    let scrollRequested = false;
    const onScroll = () => {
      const root = document.scrollingElement || document.documentElement;
      if (!scrollRequested && window.scrollY + window.innerHeight >= root.scrollHeight - 20) {
        scrollRequested = true;
        void requestData("svelte-scroll-trigger");
      }
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  });
</script>

<svelte:head>
  <title>Local SvelteKit crawl fixture</title>
</svelte:head>

<main>
  <h1>Local SvelteKit crawl fixture</h1>
  <nav aria-label="SvelteKit fixture routes">
    <a href="/svelte/guide">Guide route</a>
  </nav>
  <pre id="svelte-catalog">{JSON.stringify(catalog)}</pre>
  <form action="/api/svelte-search" method="post">
    <label>Search SvelteKit fixture <input name="query" value="svelte-local" /></label>
    <input type="hidden" name="csrf" value="local-fixture-token" />
    <button type="submit">Submit SvelteKit search</button>
  </form>
  <button id="svelte-runtime-trigger" type="button" data-api-root="api" data-api-name="svelte-click" onclick={(event) => requestData(event.currentTarget.id)}>Load runtime endpoint</button>
  <button id="svelte-menu-open" type="button" aria-haspopup="true" onclick={() => (menuOpen = true)}>Open menu</button>
  {#if menuOpen}
    <button id="svelte-menu-load" type="button" data-api-root="api" data-api-name="svelte-menu" onclick={(event) => requestData(event.currentTarget.id)}>Load menu data</button>
  {/if}
  <button id="svelte-page-next" type="button" data-api-root="api" data-api-name="svelte-page" disabled={page > 1} onclick={nextPage}>Load next page</button>
  <p>Current page: {page}</p>
  <pre>{JSON.stringify(result)}</pre>
  <span id="svelte-scroll-trigger" hidden data-api-root="api" data-api-name="svelte-scroll"></span>
  <div aria-hidden="true" style="height: 600vh"></div>
</main>
