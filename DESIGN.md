---
name: Maisongle
description: Application de recherche immobilière
colors:
  primary: "#2563eb"
  primary-hover: "#1d4ed8"
  success: "#16a34a"
  success-hover: "#15803d"
  danger: "#ef4444"
  danger-hover: "#dc2626"
  warning: "#eab308"
  surface: "#ffffff"
  background: "#f9fafb"
  text-primary: "#111827"
  text-secondary: "#4b5563"
  text-muted: "#9ca3af"
  border: "#d1d5db"
typography:
  display:
    fontFamily: "ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, 'Noto Sans', sans-serif"
    fontWeight: 700
  body:
    fontFamily: "ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, 'Noto Sans', sans-serif"
    fontWeight: 400
rounded:
  sm: "0.25rem"
  md: "0.375rem"
  lg: "0.5rem"
  xl: "0.75rem"
  full: "9999px"
spacing:
  xs: "0.5rem"
  sm: "1rem"
  md: "1.5rem"
  lg: "2rem"
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.surface}"
    rounded: "{rounded.lg}"
    padding: "0.5rem 1rem"
  button-primary-hover:
    backgroundColor: "{colors.primary-hover}"
  button-success:
    backgroundColor: "{colors.success}"
    textColor: "{colors.surface}"
    rounded: "{rounded.lg}"
    padding: "0.5rem 1rem"
  card:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.xl}"
    padding: "1.5rem"
---

# Design System: Maisongle

## 1. Overview

**Creative North Star: "The Analyst's Workbench"**

Maisongle is a strictly functional, data-dense command center built for real estate analysis. It aggregates listings and enriches them with vital third-party data without the clutter of mainstream portals. The aesthetic is clean, modern, and utilitarian: structural shadows organize the information hierarchy, while crisp boundaries keep the data focused and legible. The design explicitly rejects fragmented layouts, ad-heavy interfaces, and artificially limited experiences. 

**Key Characteristics:**
- **Density with Clarity:** High information density balanced by strict structural boundaries.
- **Data First:** The listing and its enriched context are the heroes; everything else is out of the way.
- **Structural Layers:** Shadows group related information and elevate interactive elements over the base gray canvas.

## 2. Colors

The palette relies on a stark contrast between the bright white surfaces and the subtle gray background, punctuated by functional semantic colors.

### Primary
- **Blue 600** (#2563eb): Used for primary actions, active navigation states, and highlighting key financial figures (like prices).

### Secondary & Semantic
- **Green 600** (#16a34a): Used for positive indicators, 'save' actions, and "cheap" price indicators.
- **Red 500** (#ef4444): Used for destructive actions (hiding properties, removing favorites) and "expensive" price indicators.
- **Yellow 500** (#eab308): Used for warning indicators and specific POI categories (schools).

### Neutral
- **Gray 50** (#f9fafb): The default application background.
- **White** (#ffffff): Surface color for all cards and primary content containers.
- **Gray 900** (#111827): Primary text color for headings and critical data.
- **Gray 500** (#6b7280): Secondary text color for labels, metadata, and supporting information.
- **Gray 300** (#d1d5db): Structural borders and dividers.

**The Functional Color Rule.** Color is reserved strictly for interaction and semantic meaning (good/bad/warning). It is never used purely for decoration.

## 3. Typography

**Display Font:** System Sans (Tailwind default)
**Body Font:** System Sans (Tailwind default)

**Character:** Crisp, legible, and highly optimized for data-dense screens without requiring external font loads.

### Hierarchy
- **Display** (Bold, text-2xl/3xl): Used for page titles and major property prices.
- **Headline** (Bold, text-lg/xl): Used for section headers within property details or cards.
- **Body** (Regular, text-sm/base): Used for standard descriptions and property metadata.
- **Label** (Medium, text-xs): Used for UI labels, badges, and fine-print data points.

**The Contrast Rule.** All secondary data points (Gray 500) must maintain strict contrast against the white cards to remain legible during rapid scanning.

## 4. Elevation

Shadows are structural. They lift the white surfaces (`bg-white`) off the light gray canvas (`bg-gray-50`) to create a clear hierarchy.

### Shadow Vocabulary
- **Card Shadow** (`shadow-sm`): Applied to all main content blocks, search forms, and property cards to define their boundaries.
- **Hover Shadow** (`hover:shadow-lg`, translateY): Applied to property cards on hover to indicate interactability and focus.
- **Float Shadow** (`shadow-lg`): Applied to sticky elements, popups, and the fullscreen map legend.

**The Structural Shadow Rule.** Shadows define layers, not just decoration. A shadow indicates a distinct grouping of information or an interactive surface.

## 5. Components

Components are crisp and unobtrusive, letting the data be the focus.

### Buttons
- **Shape:** Softly rounded (`rounded-lg` or `rounded-full` for icons).
- **Primary:** Blue background (`bg-blue-600`), white text, bold.
- **Hover:** Darkens slightly (`hover:bg-blue-700`) with a brief transition.
- **Icon Actions:** Ghost buttons on surfaces that reveal backgrounds on hover (`hover:bg-gray-100`), or white floating circles with a shadow (`shadow-md`) when layered over images.

### Property Cards
- **Corner Style:** Highly rounded (`rounded-xl`).
- **Background:** White (`bg-white`).
- **Shadow Strategy:** Resting at `shadow-sm`, elevating to `shadow-lg` with a `-2px` Y-translation on hover.
- **Border:** Subtle (`border border-gray-200`).
- **Internal Padding:** `p-4` or `p-6`.

### Badges / Tags
- **Style:** Small text (`text-xs`), medium weight, rounded (`rounded` or `rounded-full`), with a soft background tint corresponding to the semantic meaning (e.g., `bg-green-100 text-green-800`).

### Inputs / Fields
- **Style:** White background, subtle border (`border-gray-300`), softly rounded (`rounded-lg`).
- **Focus:** Sharp blue focus ring (`focus:ring-2 focus:ring-blue-500`) with a transparent border to prevent layout shifts.

## 6. Do's and Don'ts

### Do:
- **Do** use `bg-white` inside `shadow-sm` containers to group information logically on the `bg-gray-50` background.
- **Do** rely on Tailwind's system fonts to keep the application feeling like a native utility.
- **Do** ensure interactive elements (cards, buttons) have clear hover states (color shifts or elevation).

### Don't:
- **Don't** use fragmented layouts or infinite scrolling patterns typical of mainstream portals.
- **Don't** use decorative colors; every color shift should signify an interaction or data state.
- **Don't** use gradient text or glassmorphism.
- **Don't** animate images on hover (`.group:hover .group-hover:scale` on `<img>` elements).
