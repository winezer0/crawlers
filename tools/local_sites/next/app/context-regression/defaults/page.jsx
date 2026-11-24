import SharedProbe from "../_components/SharedProbe";
export default function Page() {
  const props = { options: {} };
  return <main><h1>Default and spread inputs</h1><SharedProbe {...props} /></main>;
}
