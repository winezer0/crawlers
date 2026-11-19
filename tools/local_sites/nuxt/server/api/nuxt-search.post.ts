export default defineEventHandler(async (event) => {
  const body = await readBody<Record<string, unknown>>(event);
  return {
    route: event.path,
    method: event.method,
    received: body,
  };
});
