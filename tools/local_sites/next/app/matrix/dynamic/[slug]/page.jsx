export const dynamic = "force-dynamic";
export default async function Page({ params }) {
  const { slug } = await params;
  return <main><h1>Dynamic route</h1><a href={"/api/matrix/" + encodeURIComponent(slug)}>Dynamic target</a></main>;
}
