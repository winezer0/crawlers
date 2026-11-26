(() => {
  const variant = document.body.dataset.variant;
  let state;
  let menu;
  const sent = new Set();
  const readLabel = value => value.length;
  readLabel('/actions/not-a-request/decoy');
  async function send(kind) {
    if (!state || sent.has(kind)) return;
    sent.add(kind);
    const path = '/' + [state.collection, state.token, kind].join('/');
    await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Runtime-Token': state.token },
      body: JSON.stringify({ action: kind, variant, proof: state.token }),
    });
  }
  const click = document.getElementById('click');
  click.addEventListener('click', () => send('click'));
  document.getElementById('menu').addEventListener('click', () => {
    if (menu) return;
    menu = document.createElement('button');
    menu.textContent = 'Load nested action';
    menu.type = 'button';
    menu.addEventListener('click', () => send('nested-menu'));
    document.body.appendChild(menu);
  });
  let page = 1;
  document.getElementById('page').addEventListener('click', () => {
    if (page === 1) { page++; void send('page-' + page); }
  });
  window.addEventListener('scroll', () => {
    void send('scroll-event');
    if (window.scrollY + window.innerHeight >= document.documentElement.scrollHeight - 20)
      void send('scroll-bottom');
  });
  fetch('/state?variant=' + encodeURIComponent(variant)).then(r => r.json()).then(data => {
    state = data;
    void send('response-chain');
    setTimeout(() => { void send('delayed'); }, 150);
    click.disabled = false;
  });
})();
