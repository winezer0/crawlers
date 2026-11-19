export default defineEventHandler((event) => ({
  route: event.path,
  source: getQuery(event).source ?? "unknown",
}));
