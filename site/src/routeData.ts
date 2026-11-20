import { defineRouteMiddleware, type StarlightRouteData } from '@astrojs/starlight/route-data';

type SidebarEntry = StarlightRouteData['sidebar'][number];

// the page contents open with the page's own title, not "Overview", which names the landing page in the sidebar;
// the sidebar lists the current page's sections under its entry, the same ones as the page contents
export const onRequest = defineRouteMiddleware(({ locals }) => {
	const route = locals.starlightRoute;
	const { toc, entry } = route;
	if (!toc) return;
	const [top, ...sections] = toc.items;
	if (top?.slug === '_top') top.text = entry.data.title;
	const links: SidebarEntry[] = sections.map((s) => ({
		type: 'link',
		label: s.text,
		href: `#${s.slug}`,
		isCurrent: false,
		badge: undefined,
		attrs: { class: 'sidebar-section' },
	}));
	route.sidebar = withSections(route.sidebar, links);
});

function withSections(entries: SidebarEntry[], links: SidebarEntry[]): SidebarEntry[] {
	return entries.flatMap((e) => {
		if (e.type === 'group') return [{ ...e, entries: withSections(e.entries, links) }];
		return e.isCurrent ? [e, ...links] : [e];
	});
}
