import adapterAuto from '@sveltejs/adapter-auto';
import adapterStatic from '@sveltejs/adapter-static';

// STATIC_BUILD=1 → self-contained SPA (served by FastAPI or any static host);
// otherwise adapter-auto (Vercel etc.) as before.
const adapter =
	process.env.STATIC_BUILD === '1'
		? adapterStatic({ fallback: 'index.html' })
		: adapterAuto();

/** @type {import('@sveltejs/kit').Config} */
const config = {
	compilerOptions: {
		// Force runes mode for the project, except for libraries. Can be removed in svelte 6.
		runes: ({ filename }) => (filename.split(/[/\\\\]/).includes('node_modules') ? undefined : true)
	},
	kit: {
		adapter
	}
};

export default config;
