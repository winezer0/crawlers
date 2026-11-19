const panel = document.getElementById("nested-scroll");
const action = document.getElementById("nested-scroll-button");
let requested = false;

panel.addEventListener("scroll", () => {
  const atBottom = panel.scrollTop + panel.clientHeight >= panel.scrollHeight - 2;
  if (atBottom) action.style.display = "block";
});

action.addEventListener("click", async () => {
  if (requested) return;
  requested = true;
  const prefix = String.fromCharCode(47, 97, 112, 105, 47);
  const route = ["interaction", "nested-scroll"].join("-");
  await fetch(prefix + route);
});
