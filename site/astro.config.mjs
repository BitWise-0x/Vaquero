import { defineConfig } from 'astro/config';
import { satteri } from '@astrojs/markdown-satteri';
import starlight from '@astrojs/starlight';

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
			logo: { src: '../assets/Vaquero.png', alt: 'Vaquero' },
			favicon: '/favicon.png',
			social: [{ icon: 'github', label: 'GitHub', href: 'https://github.com/BitWise-0x/Vaquero' }],
			editLink: { baseUrl: 'https://github.com/BitWise-0x/Vaquero/edit/main/site/' },
			customCss: ['./src/styles/starlight.css'],
			components: {
				Hero: './src/components/Hero.astro',
				ThemeProvider: './src/components/ThemeProvider.astro',
				Sidebar: './src/components/Sidebar.astro',
				SocialIcons: './src/components/SocialIcons.astro',
			},
			head: [
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
