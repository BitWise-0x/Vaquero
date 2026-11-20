import { defineCollection } from 'astro:content';
import { glob } from 'astro/loaders';
import { docsSchema } from '@astrojs/starlight/schema';

// the repo's docs/ folder is the single source: GitHub renders it, and the site serves it under /docs/
export const collections = {
	docs: defineCollection({
		loader: glob({
			base: '../docs',
			pattern: '*.md',
			generateId: ({ entry }) => `docs/${entry.replace(/\.md$/, '')}`,
		}),
		schema: docsSchema(),
	}),
};
