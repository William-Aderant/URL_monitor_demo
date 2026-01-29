# Quick Start Guide - Light/Dark Mode

## 🎨 What's New

Your PDF Monitor now has a beautiful light/dark mode theme switcher!

## 🚀 How to Use

### Starting the Application

```bash
# Activate virtual environment (if not already activated)
source venv/bin/activate

# Start the server
python main.py
```

The application will start at `http://localhost:8000`

### Switching Themes

1. **Look for the theme toggle button** in the top-right corner of the header
   - 🌙 Moon icon = Currently in dark mode (click to switch to light)
   - ☀️ Sun icon = Currently in light mode (click to switch to dark)

2. **Click the button** to toggle between themes
   - Smooth transition animation
   - Icon rotates when clicked
   - Your preference is saved automatically

### First Time Usage

On your first visit:
- The app detects your system's theme preference (light/dark)
- If your OS is set to dark mode, the app starts in dark mode
- If your OS is set to light mode, the app starts in light mode
- You can override this by clicking the toggle button

### Keyboard Navigation

- The theme toggle is fully keyboard accessible
- Press `Tab` to navigate to it
- Press `Enter` or `Space` to toggle

## 🎯 Features

### Dark Mode (Default)
- Easy on the eyes for extended viewing
- Perfect for low-light environments
- Modern, sleek design

### Light Mode
- High contrast for bright environments
- Traditional, clean look
- Better for printing

### Smart Features
- **Persistent**: Your choice is remembered across sessions
- **System-aware**: Respects your OS theme preference
- **Smooth**: Elegant transitions between themes
- **Universal**: All pages update instantly

## 📱 Mobile Support

The theme toggle works perfectly on mobile devices:
- Touch-friendly button
- Responsive design
- Same smooth transitions

## 🔧 Troubleshooting

### Theme not persisting?
- Check if your browser allows localStorage
- Try clearing cache and reloading

### Theme not switching?
- Make sure JavaScript is enabled
- Check browser console for errors
- Try a hard refresh (Cmd+Shift+R or Ctrl+Shift+R)

### Want to reset to default?
```javascript
// Open browser console and run:
localStorage.removeItem('theme');
location.reload();
```

## 🎨 Color Palette Reference

### Dark Theme Colors
- Background: Deep blues (#0f1419, #1a2332)
- Text: Light grays (#e7e9ea, #8b98a5)
- Accent: Bright blue (#1d9bf0)

### Light Theme Colors
- Background: White/light grays (#ffffff, #f8f9fa)
- Text: Dark grays (#1a1a1a, #6c757d)
- Accent: Professional blue (#0d6efd)

## 💡 Tips

1. **Time of Day**: Use dark mode during evening hours to reduce eye strain
2. **Presentations**: Light mode might be better for screen sharing
3. **Screenshots**: Choose the theme that best represents your brand
4. **Accessibility**: Both themes are designed with high contrast ratios

## 📸 Preview

### Dark Mode
- Modern Twitter/X-inspired design
- Professional and sleek
- Reduces blue light exposure

### Light Mode
- Clean and traditional
- High readability
- Print-friendly

Enjoy your new theme switcher! 🎉
