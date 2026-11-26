import { spawn } from 'node:child_process'
import { createWriteStream, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const repo = resolve(root, '..')
const manifest = JSON.parse(readFileSync(resolve(root, 'manifest.json'), 'utf8'))
const stamp = new Date().toISOString().replaceAll(':', '').replaceAll('.', '')
const output = resolve(repo, 'artifacts', 'crawler-comparison', `testweb-e2e-${stamp}`)
mkdirSync(output, { recursive: true })
const children = []
const results = []

function start(label, command, args, cwd, env = {}) {
  const log = createWriteStream(resolve(output, `${label}.log`), { flags: 'a' })
  const child = spawn(command, args, { cwd, env: { ...process.env, ...env }, shell: process.platform === 'win32', windowsHide: true })
  child.stdout.pipe(log); child.stderr.pipe(log)
  children.push({ child, log, label })
  return child
}

async function waitFor(url, label, timeout = 180_000) {
  const deadline = Date.now() + timeout
  let last = ''
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url, { signal: AbortSignal.timeout(3000) })
      if (response.ok) return
      last = `HTTP ${response.status}`
    } catch (error) { last = error.message }
    await new Promise(resolve => setTimeout(resolve, 1000))
  }
  throw new Error(`${label} failed to become ready: ${last}`)
}

function check(condition, message) { if (!condition) throw new Error(message) }

async function frameworkCheck(browser, site) {
  const page = await browser.newPage()
  const requests = []
  page.on('request', request => { if (request.url().includes('/api/')) requests.push({ method: request.method(), url: request.url() }) })
  try {
    await page.goto(site.url, { waitUntil: 'networkidle', timeout: 60_000 })
    const header = await page.locator('header').innerText()
    check(header.toLowerCase().includes(site.id), `${site.id}: expected site identity in header, got ${header}`)
    const marker = await page.evaluate(id => {
      if (id.includes('vue') || id.includes('todomvc') && id.startsWith('app-vue')) return Boolean(document.querySelector('#app')?.__vue_app__)
      if (id.includes('react') || id.startsWith('app-react')) return Object.keys(document.querySelector('#root') || {}).some(key => key.startsWith('__reactContainer$'))
      if (id.includes('angular') || id === 'juice-shop') return document.querySelector('app-root')?.hasAttribute('ng-version') || document.querySelector('app-root')?.getAttribute('ng-version')
      if (id === 'framework-nuxt') return Boolean(window.__NUXT__)
      if (id === 'framework-nextjs') return document.documentElement.innerHTML.includes('self.__next_f.push')
      if (id === 'framework-sveltekit') return document.body.hasAttribute('data-sveltekit-preload-data')
      return false
    }, site.id)
    check(Boolean(marker), `${site.id}: framework runtime marker missing`)
    if (site.id.includes('todomvc')) {
      await page.getByRole('textbox', { name: 'New todo' }).fill('E2E added todo')
      await page.getByRole('button', { name: 'Add todo' }).click()
      await page.getByText('E2E added todo').waitFor()
    }
    await page.getByRole('button', { name: 'Load catalog' }).click()
    await page.getByTestId('api-result').getByText(/items/).waitFor({ timeout: 10_000 })
    await page.getByRole('textbox', { name: 'Search' }).fill('crawler-check')
    await page.getByRole('button', { name: 'Search API' }).click()
    await page.getByTestId('api-result').getByText(/accepted/).waitFor({ timeout: 10_000 })
    const routeLink = page.getByRole('link', { name: 'Item 01' }).first()
    await routeLink.click()
    await page.waitForURL('**/items/item-01')
    check(requests.some(request => request.method === 'POST' && request.url.includes(`/api/${site.id}/search`)), `${site.id}: browser did not send the expected POST request`)
    return { site_id: site.id, framework: site.profile, framework_marker: true, route_navigation: true, todo_interaction: site.id.includes('todomvc'), api_get: true, api_post: true, requests }
  } finally { await page.close() }
}

async function traditionalCheck(browser, site) {
  const page = await browser.newPage()
  try {
    const response = await page.goto(site.url, { waitUntil: 'domcontentloaded' })
    check(response?.ok(), `${site.id}: home page did not return 2xx`)
    check((await page.locator('h1').innerText()).includes(site.id), `${site.id}: site title mismatch`)
    await page.locator('input[name="name"]').fill('fixed-e2e')
    await page.getByRole('button', { name: 'Send fixed test data' }).click()
    await page.getByText('accepted').waitFor()
    await page.goto(site.url, { waitUntil: 'domcontentloaded' })
    await page.getByRole('link', { name: 'Pages' }).click()
    await page.waitForURL(url => /\/pages\//i.test(url.pathname))
    await page.getByRole('link', { name: /Item 01/ }).click()
    await page.waitForURL(/detail/i)
    const base = new URL(site.url).origin + new URL(site.url).pathname
    const checks = await Promise.all(['robots.txt', 'sitemap.xml', 'assets/site.css'].map(async path => {
      const result = await fetch(new URL(path, base))
      return { path, status: result.status }
    }))
    check(checks.every(item => item.status === 200), `${site.id}: static infrastructure endpoint failed`)
    return { site_id: site.id, framework: site.profile, pages: true, form_post: true, infrastructure: checks }
  } finally { await page.close() }
}

try {
  start('backend', 'go', ['run', '.'], resolve(root, 'backend'), { GOWORK: 'off' })
  await waitFor('http://127.0.0.1:8090/healthz', 'backend')
  const apps = [
    ['vue', 'dev', ['--port', '5173']], ['react', 'dev', ['--port', '5174']],
    ['angular', 'start', ['--port', '4300']], ['angular', 'start', ['--port', '4301']],
    ['nuxt', 'dev', ['--port', '3000']], ['nextjs', 'dev', ['--port', '3001']], ['sveltekit', 'dev', ['--port', '5175']],
  ]
  for (const [app, script, args] of apps) {
    start(app + '-' + args.at(-1), 'npm', ['run', script, '--workspace', `testweb-${app}`, '--', ...args], root)
  }
  for (const site of manifest.sites) await waitFor(site.url, site.id)
  const browser = await chromium.launch({ headless: true, executablePath: process.env.TESTWEB_BROWSER || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe' })
  try {
    for (const site of manifest.sites) {
      results.push(site.profile.startsWith('traditional') || site.profile === 'html5-native-js'
        ? await traditionalCheck(browser, site)
        : await frameworkCheck(browser, site))
    }
  } finally { await browser.close() }
  writeFileSync(resolve(output, 'summary.json'), JSON.stringify({ status: 'passed', sites: results, output_dir: output }, null, 2))
  console.log(`PASS: ${results.length} sites; evidence: ${output}`)
} catch (error) {
  writeFileSync(resolve(output, 'summary.json'), JSON.stringify({ status: 'failed', error: error.stack || error.message, sites: results, output_dir: output }, null, 2))
  console.error(error.stack || error)
  process.exitCode = 1
} finally {
  for (const { child, log } of children.reverse()) {
    if (process.platform === 'win32' && child.pid) {
      spawn('taskkill', ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' })
    } else child.kill('SIGTERM')
    log.end()
  }
}
