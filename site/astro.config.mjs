import { defineConfig } from 'astro/config';
import { satteri } from '@astrojs/markdown-satteri';
import starlight from '@astrojs/starlight';
import { ogImage } from './src/og-image.mjs';

// the docs are the repo's docs/*.md, written with GitHub-style links (install.md, using.md#file-types);
// this rewrites them to the site's /docs/<page>/ routes
const docsLinks = {
	name: 'docs-links',
	link(node, ctx) {
		const m = /^([\w-]+)\.md(#.*)?$/.exec(node.url);
		if (m) ctx.setProperty(node, 'url', `/docs/${m[1]}/${m[2] ?? ''}`);
	},
};

export default defineConfig({
	site: 'https://vaquero.bitwise0x.com',
	markdown: { processor: satteri({ mdastPlugins: [docsLinks], features: { smartPunctuation: false } }) },
	integrations: [
		starlight({
			title: 'Vaquero',
			description: 'Find and round up every photo of one person, by face, on your Mac.',
			favicon: '/favicon.png',
			social: [{ icon: 'github', label: 'GitHub', href: 'https://github.com/BitWise-0x/Vaquero' }],
			editLink: { baseUrl: 'https://github.com/BitWise-0x/Vaquero/edit/main/site/' },
			customCss: ['./src/styles/starlight.css'],
			components: {
				Hero: './src/components/Hero.astro',
				MobileMenuToggle: './src/components/MobileMenuToggle.astro',
				ThemeProvider: './src/components/ThemeProvider.astro',
				Sidebar: './src/components/Sidebar.astro',
				SiteTitle: './src/components/SiteTitle.astro',
				SocialIcons: './src/components/SocialIcons.astro',
			},
			head: [
				// fixed scale: no pinch or double-tap zoom
				{ tag: 'meta', attrs: { name: 'viewport', content: 'width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no' } },
				// the browser's zoom keys, a held Ctrl or Cmd over the wheel and a trackpad pinch are not passed on; back to actual size (0) is
				{
					tag: 'script',
					content: `addEventListener('keydown', (e) => { if ((e.ctrlKey || e.metaKey) && ['+', '=', '-', '_'].includes(e.key)) e.preventDefault(); });
addEventListener('wheel', (e) => { if (e.ctrlKey || e.metaKey) e.preventDefault(); }, { passive: false });
addEventListener('gesturestart', (e) => e.preventDefault());`,
				},
				// shared by every page: link preview, icons beyond the favicon, and the web manifest
				{ tag: 'meta', attrs: { property: 'og:image', content: ogImage } },
				{ tag: 'meta', attrs: { property: 'og:image:width', content: '1200' } },
				{ tag: 'meta', attrs: { property: 'og:image:height', content: '630' } },
				{ tag: 'meta', attrs: { property: 'og:image:alt', content: 'The Vaquero icon beside the line: Find and round up every photo, by face' } },
				{ tag: 'meta', attrs: { name: 'twitter:card', content: 'summary_large_image' } },
				{ tag: 'meta', attrs: { name: 'theme-color', content: '#0c0f0e' } },
				{ tag: 'link', attrs: { rel: 'icon', href: '/favicon.ico', sizes: '32x32' } },
				{ tag: 'link', attrs: { rel: 'apple-touch-icon', href: '/apple-touch-icon.png' } },
				{ tag: 'link', attrs: { rel: 'manifest', href: '/site.webmanifest' } },
				{
					tag: 'script',
					content: `addEventListener('DOMContentLoaded', () => {
	const d = document.createElement('dialog');
	d.className = 'zoom-viewer';
	d.append(document.createElement('img'));
	d.addEventListener('click', () => d.close());
	document.body.append(d);
	document.querySelectorAll('.sl-markdown-content img').forEach((img) => {
		img.addEventListener('click', () => {
			const full = d.querySelector('img');
			full.src = img.currentSrc || img.src;
			full.alt = img.alt;
			d.showModal();
		});
	});
});`,
				},
			],
			sidebar: [
				{ label: 'Overview', link: '/' },
				{
					label: 'Docs',
					items: [
						'docs/install',
						'docs/using',
						'docs/reference-photos',
						'docs/files',
						'docs/how-it-works',
						'docs/troubleshooting',
					],
				},
			],
		}),
	],
});
