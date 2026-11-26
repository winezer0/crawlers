import React from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Link, Route, Routes } from 'react-router-dom'
import './style.css'

const site = new URLSearchParams(location.search).get('site') || import.meta.env.VITE_SITE_ID || 'framework-react'
function Home() {
  const [result, setResult] = React.useState('')
  const [todos, setTodos] = React.useState([{ id: 1, title: 'Map routes', done: false }, { id: 2, title: 'Check API methods', done: true }])
  const [draft, setDraft] = React.useState('')
  async function loadCatalog() { setResult(await (await fetch(`http://127.0.0.1:8090/api/${site}/catalog`)).text()) }
  async function submit(event) {
    event.preventDefault()
    setResult(await (await fetch(`http://127.0.0.1:8090/api/${site}/search`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ query: new FormData(event.currentTarget).get('query') }) })).text())
  }
  return <main><h1>{site} home · React Router</h1><p>A client-rendered React site with nested route data, user interaction and API requests.</p>{site === 'app-react-todomvc' && <section aria-label="TodoMVC"><h2>TodoMVC</h2><form onSubmit={e => { e.preventDefault(); if (draft.trim()) { setTodos([...todos, { id: Date.now(), title: draft.trim(), done: false }]); setDraft('') } }}><input aria-label="New todo" placeholder="What needs doing?" value={draft} onChange={e => setDraft(e.target.value)} /><button>Add todo</button></form><ul>{todos.map(todo => <li key={todo.id}><label><input type="checkbox" checked={todo.done} onChange={() => setTodos(todos.map(item => item.id === todo.id ? { ...item, done: !item.done } : item))} />{todo.title}</label></li>)}</ul><p>{todos.filter(todo => !todo.done).length} items left</p></section>}<nav><Link to="/guide">Guide</Link> <Link to="/items/item-01">Item 01</Link></nav><button onClick={loadCatalog}>Load catalog</button><form onSubmit={submit}><label>Search <input name="query" defaultValue="blue" /></label><button>Search API</button></form><pre data-testid="api-result">{result}</pre></main>
}
function Detail() {
  const id = window.location.pathname.split('/').at(-1)
  const [item, setItem] = React.useState(null)
  React.useEffect(() => { fetch(`http://127.0.0.1:8090/api/${site}/items/${id}`).then(r => r.json()).then(setItem) }, [id])
  return <main><h1>Item {id}</h1><pre>{JSON.stringify(item)}</pre><Link to="/">Back home</Link></main>
}
function App() { return <BrowserRouter><header><strong>{site}</strong><Link to="/">Home</Link></header><Routes><Route path="/" element={<Home />} /><Route path="/guide" element={<Home />} /><Route path="/items/:id" element={<Detail />} /></Routes></BrowserRouter> }
createRoot(document.getElementById('root')).render(<React.StrictMode><App /></React.StrictMode>)
