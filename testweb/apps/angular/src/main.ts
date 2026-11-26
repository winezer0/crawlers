import { Component, provideZoneChangeDetection } from '@angular/core';
import { bootstrapApplication } from '@angular/platform-browser';
import { provideRouter, RouterLink, RouterOutlet, Routes } from '@angular/router';

const site = location.port === '4301' ? 'juice-shop' : 'framework-angular';
@Component({ standalone: true, selector: 'site-home', imports: [RouterLink], template: `<h1>{{site}} · Angular Router</h1><p>A routed Angular app with a lazy page, API button and search form.</p><a routerLink="/guide">Guide</a> <a routerLink="/items/item-01">Item 01</a><button (click)="load()">Load catalog</button><form (submit)="search($event)"><label>Search <input name="query" value="blue"></label><button>Search API</button></form><pre data-testid="api-result">{{result}}</pre>` })
class HomeComponent {
  site = site; result = '';
  async load() { this.result = await (await fetch(`http://127.0.0.1:8090/api/${site}/catalog`)).text(); }
  async search(event: Event) { event.preventDefault(); const form = event.target as HTMLFormElement; this.result = await (await fetch(`http://127.0.0.1:8090/api/${site}/search`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ query: new FormData(form).get('query') }) })).text(); }
}
@Component({ standalone: true, template: `<h1>Guide</h1><p>Angular client-side route.</p><a routerLink="/">Home</a>`, imports: [RouterLink] })
class GuideComponent {}
@Component({ standalone: true, template: `<h1>Item {{id}}</h1><pre>{{item}}</pre><a routerLink="/">Home</a>`, imports: [RouterLink] })
class ItemComponent {
  id = location.pathname.split('/').at(-1) || 'item-01'; item = '';
  constructor() { fetch(`http://127.0.0.1:8090/api/${site}/items/${this.id}`).then(r => r.text()).then(v => this.item = v); }
}
@Component({ standalone: true, selector: 'app-root', imports: [RouterOutlet, RouterLink], template: `<header><strong>{{site}}</strong><a routerLink="/">Home</a></header><main><router-outlet /></main>` })
class AppComponent { site = site; }
const routes: Routes = [{ path: '', component: HomeComponent }, { path: 'guide', loadComponent: async () => GuideComponent }, { path: 'items/:id', loadComponent: async () => ItemComponent }];
bootstrapApplication(AppComponent, { providers: [provideZoneChangeDetection(), provideRouter(routes)] }).catch(err => console.error(err));
