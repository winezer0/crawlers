class RuntimeAction extends HTMLElement {
  connectedCallback() {
    const root = this.attachShadow({ mode: "open" });
    const button = document.createElement("button");
    button.id = "shadow-action";
    button.type = "button";
    button.textContent = "Load data from open shadow DOM";
    button.dataset.apiRoot = this.dataset.apiRoot;
    button.dataset.apiName = this.dataset.apiName;
    button.addEventListener("click", () => {
      const endpoint = "/" + button.dataset.apiRoot + "/" + button.dataset.apiName;
      void fetch(endpoint);
    });
    root.append(button);
  }
}

customElements.define("runtime-action", RuntimeAction);
