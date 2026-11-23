export async function run() {
  const parts = ["api", "matrix", "lazy"];
  await fetch("/" + parts.join("/"));
}
