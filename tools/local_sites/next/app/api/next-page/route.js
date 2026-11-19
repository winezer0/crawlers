export async function GET(request) {
  const url = new URL(request.url);
  return Response.json({ route: url.pathname, page: Number(url.searchParams.get("page") || 1) });
}
