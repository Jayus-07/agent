import type { Config } from 'tailwindcss'

const config: Config = {
  content: ['./src/**/*.{js,ts,jsx,tsx,mdx}'],
  theme: {
    extend: {
      colors: {
        accent: {
          DEFAULT: '#4D6BFE',
          hover: '#3b54d4',
          soft: 'rgba(77, 107, 254, 0.08)',
        },
        surface: {
          root: '#fafbfc',
          base: '#ffffff',
          elevated: '#f8f9fb',
          hover: '#f3f4f6',
        },
        sidebar: {
          glass: 'rgba(255, 255, 255, 0.72)',
        },
      },
      boxShadow: {
        'input': '0 2px 8px rgba(77, 107, 254, 0.08), 0 1px 3px rgba(0, 0, 0, 0.04)',
        'card': '0 1px 3px rgba(0, 0, 0, 0.04), 0 1px 2px rgba(0, 0, 0, 0.02)',
      },
      backdropBlur: {
        glass: '20px',
      },
    },
  },
  plugins: [],
}
export default config
