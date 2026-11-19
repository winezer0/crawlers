export default defineEventHandler((event) => ({
  route: event.path,
  page: Number(getQuery(event).page ?? 1),
}));
