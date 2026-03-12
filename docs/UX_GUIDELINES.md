# Apple-Inspired UX Guidelines

Actionable frontend design guidelines modeled on Apple's design language.

---

## 1. Design Principles

- **Clarity** — Text legible at every size, icons precise, adornments subtle.
- **Deference** — UI recedes; content is the focus. Chrome never competes with content.
- **Depth** — Visual layers and motion convey hierarchy and spatial relationships.
- **Consistency** — Familiar patterns reduce cognitive load.

**In practice:**
- Every element must have a clear purpose. If it doesn't serve the user, remove it.
- Whitespace is intentional — it reduces cognitive load and directs attention.
- Use full-viewport sections with a single focal point each.
- Navigation should be minimal: broad categories funneling to specifics.

---

## 2. Color Palette

### Core palette

| Role | Hex | Usage |
|------|-----|-------|
| Primary background | `#FFFFFF` | Main content areas |
| Secondary background | `#F5F5F7` | Alternating sections, cards |
| Primary text | `#1D1D1F` | Headlines, body (never pure `#000`) |
| Secondary text | `#6E6E73` | Captions, descriptions |
| Accent / links | `#0066CC` | Text links |
| CTA buttons | `#0071E3` | Primary actions |
| Subtle border | `#D2D2D7` | Dividers, card outlines |
| Ultra-subtle border | `#E5E5E5` | Light separators |

### Accent colors (use sparingly)

| Color | Hex |
|-------|-----|
| Green | `#34C759` |
| Orange | `#FF9500` |
| Red | `#FF3B30` |
| Purple | `#AF52DE` |

### Usage rules
- Alternate sections between `#FFFFFF` and `#F5F5F7` for visual rhythm.
- Use near-black text (`#1D1D1F`), not pure black — it softens contrast for reading comfort.
- Reserve accent colors for status indicators and marketing highlights only.

---

## 3. Typography

### Font stack

```css
font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display",
  "SF Pro Text", "Helvetica Neue", Helvetica, Arial, sans-serif;

/* Or the modern shorthand: */
font-family: system-ui, -apple-system, sans-serif;

/* Monospace: */
font-family: ui-monospace, "SF Mono", monospace;
```

**Best open-source alternative:** [Inter](https://fonts.google.com/specimen/Inter) — tall x-height, open apertures, designed for screens.

### Scale

**Display text** — fluid scaling with `clamp()`:

```css
.hero     { font-size: clamp(48px, 1rem + 5vw, 96px); font-weight: 700; line-height: 1.05; letter-spacing: -0.015em; }
h1        { font-size: clamp(40px, 1rem + 4vw, 80px); font-weight: 700; line-height: 1.07; letter-spacing: -0.015em; }
h2        { font-size: clamp(32px, 1rem + 3vw, 64px); font-weight: 700; line-height: 1.1;  letter-spacing: -0.01em; }
h3        { font-size: clamp(24px, 1rem + 2vw, 48px); font-weight: 600; line-height: 1.15; letter-spacing: -0.005em; }
h4        { font-size: clamp(20px, 1rem + 1vw, 32px); font-weight: 600; line-height: 1.2;  letter-spacing: 0; }
```

**Body text** — discrete breakpoint stepping (not `clamp()`):

```css
body {
  font-size: 16px;        /* mobile */
  line-height: 1.5;
  font-weight: 400;
  letter-spacing: -0.01em;
}

@media (min-width: 744px) {
  body { font-size: 17px; }  /* tablet+ — Apple caps body at 17px */
}
```

### Weight hierarchy

| Element | Weight |
|---------|--------|
| Headlines | 700 (Bold) |
| Subheadings | 600 (Semibold) |
| Body | 400 (Regular) |
| Captions / labels | 400, smaller size |

### Text rendering

```css
body {
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  text-rendering: optimizeLegibility;
}
```

---

## 4. Animation & Transitions

### Principles
- Motion must be **purposeful** — convey status, provide feedback, or clarify hierarchy.
- Never add motion for decoration alone.

### Duration guide

| Type | Duration |
|------|----------|
| Micro-interactions (hover, toggle) | 100–200ms |
| Element transitions (fade, slide, color) | 200–350ms |
| Page/section transitions | 300–500ms |
| Scroll-triggered reveals | 400–800ms |

### Easing curves

```css
/* Default — ease-in-out with deceleration bias */
transition-timing-function: cubic-bezier(0.25, 0.1, 0.25, 1.0);

/* Elements entering (ease-out) */
transition-timing-function: cubic-bezier(0.0, 0.0, 0.25, 1.0);

/* Elements leaving (ease-in) */
transition-timing-function: cubic-bezier(0.42, 0.0, 1.0, 1.0);
```

### Common patterns

```css
/* Hover feedback */
a, button {
  transition: opacity 0.3s ease, color 0.3s ease;
}

/* Scroll-triggered fade-in */
.reveal {
  opacity: 0;
  transform: translateY(20px);
  transition: opacity 0.6s ease-out, transform 0.6s ease-out;
}
.reveal.visible {
  opacity: 1;
  transform: translateY(0);
}

@media (prefers-reduced-motion: reduce) {
  html { scroll-behavior: auto; }
  *, *::before, *::after {
    animation: none !important;
    transition-duration: 0.01ms !important;
  }
  .reveal {
    opacity: 1;
    transform: none;
  }
}
```

```javascript
// Intersection Observer for scroll reveals
const observer = new IntersectionObserver((entries) => {
  entries.forEach(entry => {
    if (entry.isIntersecting) {
      entry.target.classList.add('visible');
      observer.unobserve(entry.target);
    }
  });
}, { threshold: 0.15 });

document.querySelectorAll('.reveal').forEach(el => observer.observe(el));
```

- Always respect `prefers-reduced-motion`; reveals should degrade to immediate visibility.
- For external links opened with `target="_blank"`, add `rel="noopener noreferrer"`.

---

## 5. Responsive Design

### Breakpoints

```css
/* Mobile-first base */
@media (min-width: 734px)  { /* tablet */  }
@media (min-width: 1024px) { /* desktop */ }
@media (min-width: 1440px) { /* large desktop */ }
```

### Container

```css
.container {
  max-width: 980px;
  margin: 0 auto;
  padding: 0 22px;
}

@media (min-width: 1024px) {
  .container { padding: 0 40px; }
}

.container--wide {
  max-width: 1440px;
}
```

### Grid

```css
.grid {
  display: grid;
  gap: 2rem;
  grid-template-columns: 1fr;
}

@media (min-width: 734px)  { .grid { grid-template-columns: repeat(2, 1fr); gap: 3rem; } }
@media (min-width: 1024px) { .grid { grid-template-columns: repeat(3, 1fr); } }
```

### Key patterns
- Full-bleed hero sections (`100vw` x `100vh`) with centered content.
- Images: `max-width: 100%; height: auto` for fluid scaling.
- Navigation collapses to hamburger on mobile.
- Generous vertical padding: 80–120px per section (desktop), 40–60px (mobile).

---

## 6. CSS Techniques

### Full-bleed hero

```css
.hero {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-direction: column;
  text-align: center;
  padding: 0 24px;
  background-color: #F5F5F7;
}
```

### "Learn more" link with animated arrow

```css
.link-more {
  color: #0066CC;
  font-size: 17px;
  text-decoration: none;
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.link-more::after {
  content: "\203A";
  font-size: 1.2em;
  transition: transform 0.3s ease;
}
.link-more:hover::after {
  transform: translateX(4px);
}
```

### Alternating sections

```css
.section:nth-child(odd)  { background-color: #FFFFFF; }
.section:nth-child(even) { background-color: #F5F5F7; }
.section { padding: 100px 0; }
```

### Subtle cards

```css
.card {
  background: #FFFFFF;
  border-radius: 18px;
  overflow: hidden;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
  transition: transform 0.3s ease, box-shadow 0.3s ease;
}
.card:hover {
  transform: scale(1.02);
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.08);
}
```

### Frosted glass navigation

```css
.nav {
  position: fixed;
  top: 0;
  width: 100%;
  background: rgba(255, 255, 255, 0.72);
  backdrop-filter: saturate(180%) blur(20px);
  -webkit-backdrop-filter: saturate(180%) blur(20px);
  border-bottom: 1px solid rgba(0, 0, 0, 0.1);
  z-index: 1000;
}
```
