export async function POST(request) {
  const form = await request.formData();
  return Response.json({
    route: "/api/next-search",
    method: "POST",
    received: Object.fromEntries(form.entries()),
  });
}
