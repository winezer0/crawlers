import SharedProbe from "../_components/SharedProbe";
export default function Page() {
  const common = { options: { label: "instances" } };
  return <main><h1>Two instances sharing one module</h1>
    <SharedProbe {...common} variant="alpha" />
    <SharedProbe variant="beta" {...common} />
  </main>;
}
