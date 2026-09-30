# Yashome brand

**Yashome** -- *Yet Another Smart Home*. The mark is the word **Yash!** where the `h` is the house:
its stem and arch are the walls, a roof with a chimney sits on top, the `!` is the far wall and the
sun is its dot.

- Typeface: [Nunito](https://fonts.google.com/specimen/Nunito) ExtraBold (SIL Open Font License).
  The SVGs carry the letters as outlines, so nothing has to be installed to view them.
- Palette, light: primary `#177A75`, sun `#F5B642`, text `#1E2B30`, muted `#5F6F74`, surface `#F3F7F7`, background `#FFFFFF`.
- Palette, dark (= the dashboard): primary `#3CC2B8`, sun `#FFC34D`, text `#E6EAF0`, muted `#8A93A3`, surface `#1A1F29`, background `#0E1116`.
- Fill (buttons and chips with white text), both schemes: `#177A75` (5.2:1). The bright `#3CC2B8` is for
  text, borders and marks on dark backgrounds only: white text on it is 2.2:1, below WCAG AA.
- The sun (`#F5B642` / `#FFC34D`) is decoration: never text on white (1.8:1); dark text on it is fine.
- Use the wordmark on a plain background, never outlined; the icon tile is the teal square with the white mark.

Files: `yash-wordmark-{light,dark}[-transparent].svg`, `yash-icon-{light,dark}[-transparent].svg`,
`yash-sheet-{light,dark}.svg`, `yash-wordmark-header.svg` (white, for the dashboard header).
`brand.py` regenerates all of them plus the PWA icons in the repository root and `docs/img/brand.png`.
