// Full client-side app: every page fetches from the FastAPI backend in the
// browser. Disabling SSR lets the static (SPA) build serve all routes from
// a single index.html fallback.
export const ssr = false;
export const prerender = false;
