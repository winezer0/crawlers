export const dynamic = "force-dynamic";
const expected = {
  alpha: "alpha:instances",
  beta: "beta:instances",
  default: "default:fallback",
  click: "click:interaction",
  state: "state:unlocked",
};
export async function GET(request, { params }) {
  const { kind } = await params;
  if (!Object.hasOwn(expected, kind))
    return Response.json({ error: "unexpected target" }, { status: 404 });
  if (request.headers.get("X-Component-Proof") !== expected[kind])
    return Response.json({ error: "missing actual component context" }, { status: 400 });
  return Response.json({ kind, proof: expected[kind] });
}
