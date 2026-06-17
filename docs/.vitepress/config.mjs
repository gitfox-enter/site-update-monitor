import { defineConfig } from 'vitepress'
import { fileURLToPath } from 'node:url'

export default defineConfig({
  base: '/RSSForge/',
  title: 'RSSForge',
  description: '⚡ 基于 GitHub Actions 的免费 RSS 订阅源生成器，给任何网站生成 RSS，无服务器、零成本、可持续更新。',
  lang: 'zh-CN',
  head: [
    ['link', { rel: 'icon', href: 'https://gitfox-enter.github.io/RSSForge/favicon.ico' }],
    ['meta', { name: 'theme-color', content: '#e91e8e' }],
  ],
  vite: {
    css: {
      preprocessorOptions: {},
    },
  },
  themeConfig: {
    logo: { src: 'https://gitfox-enter.github.io/RSSForge/favicon.ico', width: 24, height: 24 },
    siteTitle: 'RSSForge',
    nav: [
      { text: '指南', link: '/guide/introduction' },
      { text: '配置', link: '/config/sites' },
      { text: '订阅源', link: '/feeds/overview' },
      { text: 'GitHub', link: 'https://github.com/gitfox-enter/RSSForge' },
    ],
    sidebar: {
      '/guide/': [
        {
          text: '📖 指南',
          items: [
            { text: '什么是 RSSForge', link: '/guide/introduction' },
            { text: '快速开始', link: '/guide/getting-started' },
            { text: '部署到 GitHub', link: '/guide/deploy' },
            { text: '运行机制', link: '/guide/how-it-works' },
          ],
        },
      ],
      '/config/': [
        {
          text: '⚙️ 配置',
          items: [
            { text: 'sites.yaml 站点配置', link: '/config/sites' },
            { text: '分页抓取历史内容', link: '/config/pagination' },
            { text: '更新频率调度', link: '/config/schedule' },
            { text: '过滤与黑名单', link: '/config/filter' },
          ],
        },
      ],
      '/feeds/': [
        {
          text: '📡 订阅源',
          items: [
            { text: '订阅源概览', link: '/feeds/overview' },
            { text: 'OPML 批量订阅', link: '/feeds/opml' },
            { text: '阅读器推荐', link: '/feeds/readers' },
          ],
        },
      ],
    },
    socialLinks: [{ icon: 'github', link: 'https://github.com/gitfox-enter/RSSForge' }],
    footer: {
      message: '基于 GitHub Actions 运行，零服务器成本',
      copyright: `MIT License · © ${new Date().getFullYear()} RSSForge · Fork from site-update-monitor`,
    },
    editLink: {
      pattern: 'https://github.com/gitfox-enter/RSSForge/edit/main/docs/:path',
      text: '在 GitHub 上编辑此页',
    },
    search: { provider: 'local' },
  },
  markdown: {
    lineNumbers: false,
  },
})
