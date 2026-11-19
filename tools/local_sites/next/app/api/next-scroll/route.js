export async function GET(request) {
  const url = new URL(request.url);
  return Response.json({ route: url.pathname, loaded: true });
}
