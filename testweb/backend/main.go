package main

import (
	"encoding/json"
	"fmt"
	"log"
	"net"
	"net/http"
	"net/url"
	"os"
	"strings"
	"time"
)

var suffix = map[string]string{
	"testasp":    ".asp",
	"testphp":    ".php",
	"testaspnet": ".aspx",
	"testhtml5":  ".html",
}

var methods = map[string]string{"testasp": "submit.asp", "testphp": "submit.php", "testaspnet": "Submit.aspx", "testhtml5": "submit"}

func main() {
	addr := "127.0.0.1:8090"
	if len(os.Args) > 1 {
		addr = os.Args[1]
	}
	host, _, err := net.SplitHostPort(addr)
	if err != nil || net.ParseIP(host) == nil || !net.ParseIP(host).IsLoopback() {
		log.Fatal("listen address must be a loopback IP and port")
	}
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"status":"ok"}`))
	})
	mux.HandleFunc("/api/", serveAPI)
	mux.HandleFunc("/sites/", serveSite)
	server := &http.Server{Addr: addr, Handler: requestLog(mux), ReadHeaderTimeout: 5 * time.Second}
	log.Printf("testweb listening on http://%s", addr)
	log.Fatal(server.ListenAndServe())
}

func serveAPI(w http.ResponseWriter, r *http.Request) {
	origin := r.Header.Get("Origin")
	if strings.HasPrefix(origin, "http://127.0.0.1:") || strings.HasPrefix(origin, "http://localhost:") {
		w.Header().Set("Access-Control-Allow-Origin", origin)
		w.Header().Set("Vary", "Origin")
	}
	if r.Method == http.MethodOptions {
		w.Header().Set("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
		w.Header().Set("Access-Control-Allow-Headers", "Content-Type")
		w.WriteHeader(http.StatusNoContent)
		return
	}
	parts := strings.Split(strings.Trim(r.URL.Path, "/"), "/")
	if (len(parts) != 3 && len(parts) != 4) || parts[0] != "api" {
	http.NotFound(w, r)
		return
	}
	site, resource := parts[1], parts[2]
	if !knownSite(site) {
		http.NotFound(w, r)
		return
	}
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	switch {
	case resource == "catalog" && r.Method == http.MethodGet:
		_ = json.NewEncoder(w).Encode(map[string]any{"items": []map[string]string{{"id": "item-01", "name": "Blue notebook"}, {"id": "item-02", "name": "Green notebook"}}, "next": "/api/" + site + "/items/item-02"})
	case resource == "items" && len(parts) == 4 && r.Method == http.MethodGet:
		_ = json.NewEncoder(w).Encode(map[string]string{"id": parts[3], "name": "Notebook " + parts[3]})
	case resource == "search" && r.Method == http.MethodGet:
		_ = json.NewEncoder(w).Encode(map[string]string{"query": r.URL.Query().Get("q"), "method": r.Method})
	case resource == "search" && r.Method == http.MethodPost:
		var payload struct {
			Query string `json:"query"`
		}
		if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 4096)).Decode(&payload); err != nil {
			http.Error(w, "invalid JSON", http.StatusBadRequest)
			return
		}
		_ = json.NewEncoder(w).Encode(map[string]string{"query": payload.Query, "method": r.Method, "accepted": "true"})
	case resource == "interaction" && r.Method == http.MethodGet:
		_ = json.NewEncoder(w).Encode(map[string]string{"event": r.URL.Query().Get("event"), "method": r.Method})
	default:
		http.NotFound(w, r)
	}
}

func knownSite(site string) bool {
	_, traditional := suffix[site]
	if traditional {
		return true
	}
	switch site {
	case "juice-shop", "framework-vue", "framework-react", "app-react-todomvc", "app-vue-todomvc", "framework-angular", "framework-nuxt", "framework-nextjs", "framework-sveltekit":
		return true
	default:
		return false
	}
}

func requestLog(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		log.Printf("method=%s url=%q host=%q", r.Method, r.URL.RequestURI(), r.Host)
		next.ServeHTTP(w, r)
	})
}

func serveSite(w http.ResponseWriter, r *http.Request) {
	parts := strings.SplitN(strings.TrimPrefix(r.URL.Path, "/sites/"), "/", 2)
	if len(parts) != 2 {
		http.NotFound(w, r)
		return
	}
	site, rest := parts[0], "/"+parts[1]
	if _, ok := suffix[site]; !ok {
		http.NotFound(w, r)
		return
	}
	ext := suffix[site]
	if site == "testhtml5" {
		ext = ".html"
	}
	pagesPath := "/pages/"
	if site == "testaspnet" {
		pagesPath = "/Pages/"
	}
	switch {
	case rest == "/" && r.Method == http.MethodGet:
		home(w, site, extFor(site), methods[site])
	case rest == pagesPath && r.Method == http.MethodGet:
		pages(w, site, extFor(site))
	case rest == pagesPath+"detail"+ext && r.Method == http.MethodGet:
		detail(w, site, r.URL.Query().Get("id"))
	case rest == "/search"+ext && r.Method == http.MethodGet:
		search(w, r.URL.Query().Get("q"), r.URL.Query().Get("page"))
	case rest == "/"+methods[site] && r.Method == http.MethodPost:
		postForm(w, r)
	case rest == "/assets/site.css" && r.Method == http.MethodGet:
		w.Header().Set("Content-Type", "text/css; charset=utf-8")
		_, _ = w.Write([]byte("body{font-family:sans-serif} nav a{margin-right:1rem}"))
	case site == "testhtml5" && rest == "/assets/site.js" && r.Method == http.MethodGet:
		w.Header().Set("Content-Type", "application/javascript; charset=utf-8")
		_, _ = w.Write([]byte("window.testwebReady=true;"))
	case rest == "/robots.txt" && r.Method == http.MethodGet:
		w.Header().Set("Content-Type", "text/plain; charset=utf-8")
		_, _ = fmt.Fprintf(w, "User-agent: *\nAllow: /sites/%s/\nSitemap: /sites/%s/sitemap.xml\n", site, site)
	case rest == "/sitemap.xml" && r.Method == http.MethodGet:
		sitemap(w, site, ext)
	default:
		http.NotFound(w, r)
	}
}

func extFor(site string) string {
	if site == "testhtml5" {
		return ".html"
	}
	return suffix[site]
}

func base(site string) string { return "/sites/" + site }

func home(w http.ResponseWriter, site, ext, submit string) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	pagesPath := "/pages/"
	if site == "testaspnet" {
		pagesPath = "/Pages/"
	}
	_, _ = fmt.Fprintf(w, `<!doctype html><html><head><title>%s local test site</title><link rel="stylesheet" href="%s/assets/site.css"></head><body><h1>%s</h1><nav><a href="%s%s">Pages</a><a href="%s%sdetail%s?id=item-01">Detail</a><a href="%s/search%s?q=blue&amp;page=1">Search</a></nav><form method="post" action="%s/%s"><label>Name <input name="name" value="sample"></label><button>Send fixed test data</button></form>%s</body></html>`, site, base(site), site, base(site), pagesPath, base(site), pagesPath, ext, base(site), ext, base(site), submit, htmlScript(site, base(site)))
}

func htmlScript(site, prefix string) string {
	if site == "testhtml5" {
		return `<script src="` + prefix + `/assets/site.js"></script>`
	}
	return `<script>window.localRoute="` + prefix + `/pages/detail` + extFor(site) + `?id=item-01";</script>`
}

func pages(w http.ResponseWriter, site, ext string) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	pagesPath := "/pages/"
	if site == "testaspnet" {
		pagesPath = "/Pages/"
	}
	_, _ = fmt.Fprintf(w, `<!doctype html><html><body><h1>%s pages</h1><a href="%s%sdetail%s?id=item-01">Item 01</a><a href="%s/search%s?q=blue&amp;page=1">Search</a></body></html>`, site, base(site), pagesPath, ext, base(site), ext)
}

func detail(w http.ResponseWriter, site, id string) {
	if id != "item-01" {
		http.Error(w, "not found", http.StatusNotFound)
		return
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	_, _ = fmt.Fprintf(w, "<!doctype html><html><body><h1>%s item %s</h1><a href=%q>Home</a></body></html>", site, id, base(site)+"/")
}

func search(w http.ResponseWriter, query, pageNo string) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	_, _ = fmt.Fprintf(w, "<!doctype html><html><body><h1>Search</h1><p>q=%s page=%s</p></body></html>", url.QueryEscape(query), url.QueryEscape(pageNo))
}

func postForm(w http.ResponseWriter, r *http.Request) {
	if err := r.ParseForm(); err != nil {
		http.Error(w, "invalid form", http.StatusBadRequest)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	_ = json.NewEncoder(w).Encode(map[string]string{"accepted": "true", "name": r.Form.Get("name")})
}

func sitemap(w http.ResponseWriter, site, ext string) {
	w.Header().Set("Content-Type", "application/xml; charset=utf-8")
	pagesPath := "/pages/"
	if site == "testaspnet" {
		pagesPath = "/Pages/"
	}
	urls := []string{base(site) + "/", base(site) + pagesPath, base(site) + pagesPath + "detail" + ext + "?id=item-01", base(site) + "/search" + ext + "?q=blue&amp;page=1"}
	_, _ = fmt.Fprint(w, `<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">`)
	for _, item := range urls {
		_, _ = fmt.Fprintf(w, "<url><loc>%s</loc></url>", item)
	}
	_, _ = fmt.Fprint(w, `</urlset>`)
}
