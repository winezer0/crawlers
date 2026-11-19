if (window.self !== window.top) {
  const button = document.getElementById("frame-action");
  button.hidden = false;
  button.addEventListener("click", () => {
    const endpoint = "/" + button.dataset.apiRoot + "/" + button.dataset.apiName;
    void fetch(endpoint);
  });
}
