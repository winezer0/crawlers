import Link from "next/link";
import RuntimeAction from "./_components/RuntimeAction";

export default function Home() {
  return (
    <main>
      <h1>Local Next.js App Router fixture</h1>
      <nav aria-label="Next fixture routes">
        <Link href="/">Home</Link>
        <Link href="/next/guide">Guide route</Link>
        <a href="/api/next-catalog">Catalog API</a>
      </nav>
      <form action="/api/next-search" method="post">
        <label>
          Search Next fixture <input name="query" defaultValue="next-local" />
        </label>
        <input type="hidden" name="csrf" value="local-fixture-token" />
        <button type="submit">Submit Next search</button>
      </form>
      <RuntimeAction />
    </main>
  );
}
