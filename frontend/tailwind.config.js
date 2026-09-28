/** @type {import('tailwindcss').Config} */
export default {
  // 'class' rather than Tailwind's default 'media'. The theme switch is an
  // explicit user choice that has to survive the OS changing its mind, and it
  // has to be readable from JS on first paint. 'media' cannot be overridden at
  // runtime, so the toggle would be unable to override it.
  darkMode: 'class',
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      // Every colour resolves to a CSS variable defined in index.css, which is
      // redeclared under `.dark`. This is the only reason a theme switch can
      // work at all: components reference semantic names, never literal
      // gray-900, so nothing hardcodes a value that dark mode would contradict.
      colors: {
        canvas: "var(--canvas)",
        surface: {
          DEFAULT: "var(--surface)",
          hover: "var(--surface-hover)",
        },
        line: {
          DEFAULT: "var(--border)",
          strong: "var(--border-strong)",
          // Tinted borders for the coloured alert panels, which pair a soft
          // fill with a soft edge. Using the saturated `--danger`/`--success`
          // values here gave a harsh outline around a pale box.
          accent: "var(--line-accent)",
          success: "var(--line-success)",
          danger: "var(--line-danger)",
          warning: "var(--line-warning)",
        },
        body: "var(--text)",
        muted: "var(--text-muted)",
        subtle: "var(--text-subtle)",
        accent: {
          DEFAULT: "var(--accent)",
          hover: "var(--accent-hover)",
          // Two different foregrounds, deliberately not the same value:
          //   fg       - text on an --accent-soft tint (the dark accent hue)
          //   contrast - text on a solid --accent fill (near-white, except in
          //              dark mode where the fill is bright enough to want
          //              dark text)
          fg: "var(--accent-contrast)",
          contrast: "var(--accent-contrast)",
          soft: "var(--accent-soft)",
          // Focus ring, deliberately lighter than the fill in dark mode so it
          // stays visible against a dark surface.
          ring: "var(--accent-ring)",
        },
        success: {
          DEFAULT: "var(--success)",
          hover: "var(--success-hover)",
          fg: "var(--success-fg)",
          contrast: "var(--success-contrast)",
          soft: "var(--success-soft)",
        },
        danger: {
          DEFAULT: "var(--danger)",
          hover: "var(--danger-hover)",
          fg: "var(--danger-fg)",
          contrast: "var(--danger-contrast)",
          soft: "var(--danger-soft)",
        },
        warning: {
          DEFAULT: "var(--warning)",
          fg: "var(--warning-fg)",
          contrast: "var(--warning-contrast)",
          soft: "var(--warning-soft)",
        },
      },
      // NOTE: no borderRadius override. This file used to redefine lg/md/sm as
      // `var(--radius)` with --radius never defined anywhere, which silently
      // dropped border-radius on every rounded-md / rounded-lg in the app -
      // all 43 of them, including every card, button and input. Restoring
      // Tailwind's defaults is the fix; --radius no longer exists.
    },
  },
  plugins: [],
}
