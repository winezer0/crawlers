export default defineEventHandler((event) => {
  return {
    route: event.path,
    items: ["nuxt-alpha", "nuxt-beta"],
  };
});
