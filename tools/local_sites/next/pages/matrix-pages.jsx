export async function getServerSideProps() {
  return { props: { target: "/api/matrix/pages-ssr" } };
}
export default function Page({ target }) {
  return <main><h1>Pages Router SSR</h1><a href={target}>Pages target</a></main>;
}
