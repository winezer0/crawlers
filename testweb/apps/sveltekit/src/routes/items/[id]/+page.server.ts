export async function load({ params }) {
  const item = await fetch(`http://127.0.0.1:8090/api/framework-sveltekit/items/${params.id}`).then(response => response.json())
  return { id: params.id, item }
}
