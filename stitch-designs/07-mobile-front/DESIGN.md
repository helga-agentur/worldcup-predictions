---
name: Helga Helvetica
colors:
  surface: '#fcf8ff'
  surface-dim: '#d7d6ff'
  surface-bright: '#fcf8ff'
  surface-container-lowest: '#ffffff'
  surface-container-low: '#f5f2ff'
  surface-container: '#eeecff'
  surface-container-high: '#e8e6ff'
  surface-container-highest: '#e1e0ff'
  on-surface: '#17183a'
  on-surface-variant: '#454558'
  inverse-surface: '#2c2d50'
  inverse-on-surface: '#f2efff'
  outline: '#757589'
  outline-variant: '#c5c4db'
  surface-tint: '#343dff'
  primary: '#0001bb'
  on-primary: '#ffffff'
  primary-container: '#0000ff'
  on-primary-container: '#b3b7ff'
  inverse-primary: '#bec2ff'
  secondary: '#1e6d00'
  on-secondary: '#ffffff'
  secondary-container: '#93f86e'
  on-secondary-container: '#1f7200'
  tertiary: '#2c3b00'
  on-tertiary: '#ffffff'
  tertiary-container: '#3f5300'
  on-tertiary-container: '#a0cc00'
  error: '#ba1a1a'
  on-error: '#ffffff'
  error-container: '#ffdad6'
  on-error-container: '#93000a'
  primary-fixed: '#e0e0ff'
  primary-fixed-dim: '#bec2ff'
  on-primary-fixed: '#00006e'
  on-primary-fixed-variant: '#0000ef'
  secondary-fixed: '#96fa71'
  secondary-fixed-dim: '#7bdd58'
  on-secondary-fixed: '#042100'
  on-secondary-fixed-variant: '#145200'
  tertiary-fixed: '#c2f427'
  tertiary-fixed-dim: '#a8d700'
  on-tertiary-fixed: '#161f00'
  on-tertiary-fixed-variant: '#3b4d00'
  background: '#fcf8ff'
  on-background: '#17183a'
  surface-variant: '#e1e0ff'
typography:
  display-lg:
    fontFamily: Hanken Grotesk
    fontSize: 48px
    fontWeight: '800'
    lineHeight: '1.1'
    letterSpacing: -0.02em
  headline-md:
    fontFamily: Hanken Grotesk
    fontSize: 24px
    fontWeight: '700'
    lineHeight: '1.2'
    letterSpacing: -0.01em
  headline-sm:
    fontFamily: Hanken Grotesk
    fontSize: 18px
    fontWeight: '700'
    lineHeight: '1.2'
  body-md:
    fontFamily: Chivo
    fontSize: 14px
    fontWeight: '400'
    lineHeight: '1.5'
  body-sm:
    fontFamily: Chivo
    fontSize: 13px
    fontWeight: '400'
    lineHeight: '1.4'
  data-num:
    fontFamily: Chivo
    fontSize: 16px
    fontWeight: '700'
    lineHeight: '1.2'
    letterSpacing: -0.02em
  label-caps:
    fontFamily: JetBrains Mono
    fontSize: 11px
    fontWeight: '500'
    lineHeight: '1'
    letterSpacing: 0.05em
  status-badge:
    fontFamily: Hanken Grotesk
    fontSize: 11px
    fontWeight: '800'
    lineHeight: '1'
spacing:
  unit: 4px
  gutter: 16px
  margin-mobile: 16px
  margin-desktop: 32px
  table-row-height: 40px
  max-width: 1140px
---

## Brand & Style

The design system is rooted in **Swiss Editorial** principles, specifically tailored for the high-velocity, data-dense environment of sports tournament predictions. The brand personality is authoritative, objective, and "no-nonsense." It prioritizes information utility over decorative flair, aiming to evoke a feeling of professional reliability and mathematical precision.

**Style: Swiss Minimalism / Data-Rich Editorial**
- **Structured:** A rigid adherence to a typographic grid and vertical rhythm.
- **Efficient:** Maximum information density without sacrificing legibility.
- **Trustworthy:** A clean "Paper" and "Ink" foundation that feels like a premium sports broadsheet or a technical financial report.
- **Functional:** Use of functional color for status signaling rather than brand expression.

## Colors

This design system utilizes a high-contrast palette optimized for legibility and clear data categorization.

- **Primary Brand (Helga Blue):** Reserved for the logo, primary actions, and "Live" or active indicators.
- **Base Tones:** "Paper" serves as the primary surface, "Mist" for secondary containers or hover states, and "Line" for structural dividers.
- **Result Palette:** A semantic system for prediction outcomes. Green denotes success (Exact/Correct), Red denotes failure, and Orange serves as a warning or "Locked" state.

**Dark Mode Implementation:**
In Dark Mode, the "Paper" and "Ink" colors invert. Surfaces shift to a deep navy (#030326) to maintain the "Ink" character while reducing eye strain. The result palette should maintain its hex values but may use semi-transparent fills on dark backgrounds to prevent vibrance jarring.

## Typography

The typography strategy focuses on "vertical compactness." **Hanken Grotesk** (serving as a Degular alternative) provides a bold, contemporary voice for branding and major headers. **Chivo** (serving as a Cadiz alternative) is used for the bulk of the data and body text due to its high legibility at small sizes.

**Monospacing:**
**JetBrains Mono** is introduced for labels and technical metadata (like JSON API links) to reinforce the "scientific" and "data-driven" nature of the pool.

**Hierarchy Rules:**
- Headlines use tight line-heights and negative letter-spacing for an editorial feel.
- Numeric data in tables should utilize tabular lining (if available) to ensure vertical alignment of scores and percentages.

## Layout & Spacing

The design system uses a **Fixed Grid** model on desktop to mimic a newspaper column, and a **Fluid Grid** on mobile.

- **Baseline Grid:** All elements are snapped to a 4px baseline grid to maintain the tight vertical rhythm required for sports data.
- **Data Tables:** These are the heart of the system. They use a compressed 40px row height with subtle horizontal separators ("Line" color). 
- **Match Cards:** Built with a "Split-Layout"—Teams on the left, Predictions/Scores on the right.
- **Breakpoints:**
  - **Mobile (<768px):** Single column, condensed margins (16px), full-width match cards.
  - **Desktop (>1024px):** 12-column grid within a 1140px container. Content is centered.

## Elevation & Depth

To maintain the Swiss/Modernist aesthetic, this design system avoids traditional drop shadows.

- **Flat Layering:** Depth is conveyed through color blocks (Paper vs. Mist). 
- **Low-Contrast Outlines:** Instead of shadows, cards and inputs use a 1px solid border in the "Line" color (#d7dce5).
- **Active State:** Elements being hovered or selected receive a 2px "Helga Blue" border or a slight shift in background color from Paper to Mist.
- **Match Cards:** Use a thin outline to define their boundary, with no elevation. The focus remains on the internal hierarchy of typography and color segments.

## Shapes

The shape language is strictly **Sharp (0px)**. 

- **Hard Edges:** All buttons, badges, match cards, and probability bars must have 90-degree corners. This reinforces the technical, editorial, and "precise" nature of the data.
- **Probability Bars:** Rectangular segments with no rounding. When multiple segments meet, they should be flush.
- **Status Badges:** Small, rectangular blocks of color with high-contrast text.

## Components

**1. Match Cards**
- Layout: Three-tier structure. Top: Tournament stage/date (Label Caps). Middle: Team flags + Names (Headline SM) + Current Score. Bottom: Probability bar + prediction tags.
- Boundary: 1px "Line" border.

**2. Probability Bars**
- Height: 6px or 8px.
- Construction: A background track in "Mist" with segments filled in "Helga Blue" (Home win), "Line" (Draw), and "Ink" (Away win). Use percentage widths for fills.

**3. Status Badges**
- Sizing: Compressed height.
- Semantic Fills: Use "Exact Green" for 'EXACT', "Secondary Green" for 'CORRECT', "Red" for 'WRONG'.
- Typography: Status-badge role (Uppercase, Bold).

**4. Data Tables**
- Header: Sticky top with "Mist" background, "Label Caps" typography.
- Cell Padding: 8px horizontal, 4px vertical.
- Highlighting: Hovering a row changes the background to "Mist."

**5. Navigation (Header)**
- Layout: Horizontal flex. Logo on left, nav links center, utilities (Language/Dark Mode) right.
- Links: Bold Sans, no underline unless hovered.

**6. Input Fields**
- Style: Underlined or fully boxed with 1px "Line" border. No rounding. Focus state is a 2px Helga Blue bottom border.