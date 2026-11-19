import { Component, HostListener, OnInit } from "@angular/core";
import { JsonPipe } from "@angular/common";
import { bootstrapApplication } from "@angular/platform-browser";
import { provideRouter, RouterLink, RouterOutlet, Routes } from "@angular/router";

@Component({
  standalone: true,
  selector: "app-guide",
  template: "<article><h2>Angular guide route</h2><p>Static route fixture</p></article>",
})
class GuideComponent {}

const routes: Routes = [{ path: "angular/guide", component: GuideComponent }];

@Component({
  standalone: true,
  selector: "app-root",
  imports: [JsonPipe, RouterLink, RouterOutlet],
  template: `
    <main>
      <h1>Local Angular crawl fixture</h1>
      <nav aria-label="Angular fixture routes">
        <a routerLink="/angular/guide">Guide route</a>
      </nav>
      <pre id="angular-catalog">{{ catalog | json }}</pre>
      <form action="/api/angular-search" method="post">
        <label>Search Angular fixture <input name="query" value="angular-local" /></label>
        <input type="hidden" name="csrf" value="local-fixture-token" />
        <button type="submit">Submit Angular search</button>
      </form>
      <button id="angular-runtime-trigger" type="button" data-api-root="api" data-api-name="angular-click" (click)="requestData('angular-runtime-trigger')">Load runtime endpoint</button>
      <button id="angular-menu-open" type="button" aria-haspopup="true" (click)="menuOpen = true">Open menu</button>
      @if (menuOpen) {
        <button id="angular-menu-load" type="button" data-api-root="api" data-api-name="angular-menu" (click)="requestData('angular-menu-load')">Load menu data</button>
      }
      <button id="angular-page-next" type="button" data-api-root="api" data-api-name="angular-page" [disabled]="page > 1" (click)="nextPage()">Load next page</button>
      <p>Current page: {{ page }}</p>
      <pre>{{ interactionResult | json }}</pre>
      <router-outlet></router-outlet>
      <span id="angular-scroll-trigger" hidden data-api-root="api" data-api-name="angular-scroll"></span>
      <div aria-hidden="true" style="height: 1400px"></div>
    </main>
  `,
})
class AppComponent implements OnInit {
  catalog: unknown = null;
  interactionResult: unknown = null;
  menuOpen = false;
  page = 1;
  private scrollRequested = false;

  ngOnInit(): void {
    void fetch("/api/angular-catalog")
      .then((response) => response.json())
      .then((result: unknown) => (this.catalog = result));
  }

  async requestData(elementID: string, suffix = ""): Promise<void> {
    const target = document.getElementById(elementID);
    if (!target?.dataset.apiRoot || !target.dataset.apiName) return;
    const endpoint = `/${target.dataset.apiRoot}/${target.dataset.apiName}`;
    const response = await fetch(`${endpoint}${suffix}`);
    const result = await response.json();
    this.interactionResult = result;
  }

  nextPage(): void {
    this.page += 1;
    void this.requestData("angular-page-next", `?page=${this.page}`);
  }

  @HostListener("window:scroll")
  onWindowScroll(): void {
    if (this.scrollRequested) return;
    const root = document.scrollingElement || document.documentElement;
    if (window.scrollY + window.innerHeight >= root.scrollHeight - 20) {
      this.scrollRequested = true;
      void this.requestData("angular-scroll-trigger");
    }
  }
}

bootstrapApplication(AppComponent, { providers: [provideRouter(routes)] }).catch((error: unknown) => {
  console.error(error);
});
