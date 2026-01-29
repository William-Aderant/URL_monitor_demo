# Light/Dark Mode Theme Update

## Overview
Added a comprehensive light/dark mode theme switcher to the PDF Monitor application while maintaining the modern UI design.

## Changes Made

### 1. **Base Template (`templates/base.html`)**
- Added light theme CSS variables
- Added theme toggle button in header with sun/moon icons
- Implemented JavaScript for theme switching with localStorage persistence
- Added automatic system preference detection
- Smooth transitions between themes
- Accessibility improvements with screen reader announcements

### 2. **Stylesheet (`static/style.css`)**
- Added smooth transitions for theme changes
- Light mode specific styling for:
  - Badges (success, warning, error, info)
  - Row highlighting (changed/error rows)
  - Buttons (warning, success)
  - Diff preview
  - Code blocks
- Added screen reader only utility class for accessibility
- Responsive adjustments for mobile devices
- Enhanced print styles

### 3. **Templates (`templates/changes.html`)**
- Updated hardcoded colors to use CSS variables for better theme compatibility
- Ensured relocated URL indicators work in both themes

## Features

### Theme Toggle Button
- Located in the header next to the API counter
- Click to switch between light and dark modes
- Smooth animation on toggle
- Persists preference in localStorage

### System Preference Detection
- Automatically detects OS theme preference on first visit
- Respects user's system settings (prefers-color-scheme)
- Syncs with system theme changes if no manual preference is set

### Accessibility
- ARIA labels for screen readers
- Keyboard accessible toggle button
- Smooth transitions that respect user preferences
- High contrast in both themes

## Color Schemes

### Dark Theme (Default)
- Background: Deep blues and grays (#0f1419, #1a2332, #242f3d)
- Text: Light grays (#e7e9ea, #8b98a5)
- Accent: Bright blue (#1d9bf0)
- Success: Green (#00ba7c)
- Warning: Orange (#ffad1f)
- Error: Red (#f4212e)

### Light Theme
- Background: White and light grays (#ffffff, #f8f9fa, #e9ecef)
- Text: Dark grays (#1a1a1a, #6c757d)
- Accent: Blue (#0d6efd)
- Success: Green (#198754)
- Warning: Yellow (#ffc107)
- Error: Red (#dc3545)

## Usage

1. Start the server:
   ```bash
   python main.py
   ```

2. Open the application in your browser

3. Click the theme toggle button (🌙/☀️) in the header to switch between themes

4. Your preference will be saved and persist across sessions

## Technical Details

### CSS Variables
All colors use CSS variables for easy theme switching:
- `--bg-primary`, `--bg-secondary`, `--bg-tertiary`
- `--text-primary`, `--text-secondary`
- `--accent`, `--accent-hover`
- `--success`, `--warning`, `--error`
- `--border`

### Theme Persistence
- Stored in `localStorage` with key `theme`
- Falls back to system preference if not set
- Applies immediately on page load

### Smooth Transitions
- 0.3s ease transitions on theme-sensitive elements
- Rotation animation on theme toggle icon
- No jarring color changes

## Browser Support
- All modern browsers (Chrome, Firefox, Safari, Edge)
- Requires JavaScript for theme switching
- Gracefully falls back to dark theme if localStorage is unavailable

## Future Enhancements
- Auto theme switching based on time of day
- Custom color scheme options
- High contrast mode
- Color blindness friendly palettes
