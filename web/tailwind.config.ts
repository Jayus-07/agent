import type { Config } from 'tailwindcss'

const config: Config = {
  content: ['./src/**/*.{js,ts,jsx,tsx,mdx}'],
  theme: {
    extend: {
      colors: {
        // ChatGPT 风格配色
        sidebar: {
          bg: '#171717',
          hover: '#2f2f2f',
          active: '#2f2f2f',
        },
        chat: {
          bg: '#212121',
          user: '#2f2f2f',
          assistant: '#212121',
        },
      },
      typography: {
        DEFAULT: {
          css: {
            maxWidth: 'none',
            pre: {
              backgroundColor: '#1e1e1e',
              borderRadius: '0.5rem',
              padding: '1rem',
            },
            code: {
              backgroundColor: '#1e1e1e',
              borderRadius: '0.25rem',
              padding: '0.125rem 0.25rem',
              fontSize: '0.875em',
            },
            'pre code': {
              backgroundColor: 'transparent',
              padding: '0',
            },
          },
        },
      },
    },
  },
  plugins: [],
}
export default config
