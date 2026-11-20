import { defineRouteMiddleware } from '@astrojs/starlight/route-data';

// the page contents open with the page's own title, not "Overview", which names the landing page in the sidebar
export const onRequest = defineRouteMiddleware(({ locals }) => {
	const { toc, entry } = locals.starlightRoute;
	const top = toc?.items[0];
	if (top?.slug === '_top') top.text = entry.data.title;
});
