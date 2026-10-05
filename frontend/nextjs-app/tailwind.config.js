/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./app/**/*.{js,ts,jsx,tsx}", "./components/**/*.{js,ts,jsx,tsx}"],
  theme: {
    borderRadius: {
      none: "0",
      sm: "4px",
      DEFAULT: "6px",
      md: "8px",
      lg: "8px",
      xl: "10px",
      full: "9999px",
    },
    extend: {
      colors: {
        canvas: "rgb(var(--palette-canvas) / <alpha-value>)",
        panel: "rgb(var(--palette-panel) / <alpha-value>)",
        soft: "rgb(var(--palette-soft) / <alpha-value>)",
        ink: {
          DEFAULT: "rgb(var(--palette-ink) / <alpha-value>)",
          subtle: "rgb(var(--palette-ink-subtle) / <alpha-value>)",
          hover: "rgb(var(--palette-ink-hover) / <alpha-value>)",
        },
        muted: {
          DEFAULT: "rgb(var(--palette-muted) / <alpha-value>)",
          soft: "rgb(var(--palette-muted-soft) / <alpha-value>)",
        },
        line: {
          DEFAULT: "rgb(var(--palette-line) / <alpha-value>)",
          soft: "rgb(var(--palette-line-soft) / <alpha-value>)",
          strong: "rgb(var(--palette-line-strong) / <alpha-value>)",
        },
        brand: {
          DEFAULT: "rgb(var(--palette-brand) / <alpha-value>)",
          hover: "rgb(var(--palette-brand-hover) / <alpha-value>)",
          soft: "rgb(var(--palette-brand-soft) / <alpha-value>)",
          ink: "rgb(var(--palette-brand-ink) / <alpha-value>)",
        },
        analytic: {
          DEFAULT: "rgb(var(--palette-analytic) / <alpha-value>)",
          hover: "rgb(var(--palette-analytic-hover) / <alpha-value>)",
          soft: "rgb(var(--palette-analytic-soft) / <alpha-value>)",
        },
        success: {
          DEFAULT: "rgb(var(--palette-success) / <alpha-value>)",
          ink: "rgb(var(--palette-success-ink) / <alpha-value>)",
          soft: "rgb(var(--palette-success-soft) / <alpha-value>)",
        },
        warning: {
          DEFAULT: "rgb(var(--palette-warning) / <alpha-value>)",
          ink: "rgb(var(--palette-warning-ink) / <alpha-value>)",
          soft: "rgb(var(--palette-warning-soft) / <alpha-value>)",
        },
        danger: {
          DEFAULT: "rgb(var(--palette-danger) / <alpha-value>)",
          hover: "rgb(var(--palette-danger-hover) / <alpha-value>)",
          ink: "rgb(var(--palette-danger-ink) / <alpha-value>)",
          soft: "rgb(var(--palette-danger-soft) / <alpha-value>)",
        },
      },
    },
  },
  plugins: [],
};
