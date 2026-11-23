export const dynamic = "force-dynamic";
export async function GET(request, { params }) {
  const { kind } = await params;
  if (kind === "decoy-not-a-request")
    return Response.json({ error: "negative control was requested" }, { status: 404 });
  if (kind === "bootstrap")
    return Response.json({ next: "/api/matrix/" + ["response", "followup"].join("-") + "?token=local%2Bproof" });
  return Response.json({ kind, query: new URL(request.url).search });
}
