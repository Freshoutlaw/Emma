# Live Desktop Streaming & Enhanced Browser Interaction

Emma now supports real-time desktop viewing and comprehensive browser interaction capabilities.

## 🎥 Live Desktop Streaming

### WebSocket Streaming
**Endpoint:** `WS /api/live/desktop/stream`

Real-time desktop streaming at ~2 FPS with base64-encoded images.

```javascript
const ws = new WebSocket('ws://localhost:8000/api/live/desktop/stream');
ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  if (data.type === 'frame') {
    const img = document.getElementById('desktop-view');
    img.src = `data:image/png;base64,${data.image}`;
  } else if (data.type === 'error') {
    console.error('Stream error:', data.message);
  }
};
```

### Single Screenshot
**Endpoint:** `GET /api/live/desktop/screenshot`

Get a single desktop screenshot as PNG image.

```bash
curl http://localhost:8000/api/live/desktop/screenshot --output screenshot.png
```

## 🖱️ Desktop Interaction

### Move Mouse
**Endpoint:** `POST /api/live/desktop/mouse/move`

Move mouse to specific coordinates.

```bash
curl -X POST http://localhost:8000/api/live/desktop/mouse/move \
  -H "Content-Type: application/json" \
  -d '{"x": 500, "y": 300}'
```

**Response:**
```json
{
  "success": true,
  "x": 500,
  "y": 300
}
```

### Click
**Endpoint:** `POST /api/live/desktop/mouse/click`

Click at specific coordinates with button selection.

```bash
curl -X POST http://localhost:8000/api/live/desktop/mouse/click \
  -H "Content-Type: application/json" \
  -d '{"x": 500, "y": 300, "button": "left"}'
```

**Buttons:** `left`, `right`, `middle`

### Type Text
**Endpoint:** `POST /api/live/desktop/type`

Type text using keyboard simulation.

```bash
curl -X POST http://localhost:8000/api/live/desktop/type \
  -H "Content-Type: application/json" \
  -d '{"text": "Hello World", "interval": 0.02}'
```

### Press Key
**Endpoint:** `POST /api/live/desktop/key`

Press keyboard keys with automatic mapping.

```bash
curl -X POST http://localhost:8000/api/live/desktop/key \
  -H "Content-Type: application/json" \
  -d '{"key": "enter"}'
```

**Supported Keys:**
- `enter`, `return` → Enter key
- `escape` → Escape key
- `tab` → Tab key
- `space` → Space key
- `backspace` → Backspace key
- `delete` → Delete key
- `up`, `down`, `left`, `right` → Arrow keys

## 🌐 Enhanced Browser Interaction

### Unified Browser Action
**Endpoint:** `POST /api/live/browser/action`

All browser interactions through a single endpoint.

#### Click Element
```bash
curl -X POST http://localhost:8000/api/live/browser/action \
  -H "Content-Type: application/json" \
  -d '{"action": "click", "selector": "#submit-button"}'
```

#### Fill Form Field
```bash
curl -X POST http://localhost:8000/api/live/browser/action \
  -H "Content-Type: application/json" \
  -d '{"action": "fill", "selector": "#username", "value": "user123"}'
```

#### Scroll Page
```bash
curl -X POST http://localhost:8000/api/live/browser/action \
  -H "Content-Type: application/json" \
  -d '{"action": "scroll", "pixels": 500}'
```

#### Hover Element
```bash
curl -X POST http://localhost:8000/api/live/browser/action \
  -H "Content-Type: application/json" \
  -d '{"action": "hover", "selector": "#menu-item"}'
```

#### Press Key in Browser
```bash
curl -X POST http://localhost:8000/api/live/browser/action \
  -H "Content-Type: application/json" \
  -d '{"action": "press_key", "value": "enter"}'
```

#### Select Dropdown Option
```bash
curl -X POST http://localhost:8000/api/live/browser/action \
  -H "Content-Type: application/json" \
  -d '{"action": "select_option", "selector": "#country", "value": "us"}'
```

#### Navigate Back/Forward
```bash
# Go back
curl -X POST http://localhost:8000/api/live/browser/action \
  -H "Content-Type: application/json" \
  -d '{"action": "go_back"}'

# Go forward
curl -X POST http://localhost:8000/api/live/browser/action \
  -H "Content-Type: application/json" \
  -d '{"action": "go_forward"}'
```

### Get Text
**Endpoint:** `GET /api/live/browser/text`

Get text from page or specific element.

```bash
# Get entire page text
curl http://localhost:8000/api/live/browser/text

# Get specific element text
curl "http://localhost:8000/api/live/browser/text?selector=#content"
```

### Get Attribute
**Endpoint:** `GET /api/live/browser/attribute`

Get attribute value from element.

```bash
curl "http://localhost:8000/api/live/browser/attribute?selector=#link&attribute=href"
```

## 🛠️ Tool Catalog Integration

Emma can now use these capabilities through her reasoning system:

### Desktop Tools
- `desktop_move_mouse` - Move mouse to coordinates
- `desktop_click` - Click at coordinates
- `desktop_type` - Type text
- `desktop_press_key` - Press keyboard keys

### Browser Tools
- `browser_click` - Click elements by selector
- `browser_fill` - Fill form fields
- `browser_scroll` - Scroll pages
- `browser_hover` - Hover over elements
- `browser_press_key` - Press keys in browser
- `browser_select_option` - Select dropdowns
- `browser_go_back` - Navigate back
- `browser_go_forward` - Navigate forward
- `browser_get_text` - Get element text
- `browser_get_attribute` - Get attributes

## 🔒 Security

All interactions are protected by:
- **Guardian consent system** - User approval required for sensitive actions
- **Network gate** - Web operations can be blocked
- **Audit logging** - All actions are logged
- **Tool allowlist** - Per-agent tool scoping

## 📋 Example Usage

### Emma Commands

**Desktop Control:**
```
User: "Move mouse to position 500, 300"
Emma: Uses desktop_move_mouse tool

User: "Click at 500, 300"
Emma: Uses desktop_click tool

User: "Type 'Hello World'"
Emma: Uses desktop_type tool

User: "Press Enter"
Emma: Uses desktop_press_key tool
```

**Browser Automation:**
```
User: "Click the submit button"
Emma: Uses browser_click with selector "#submit-button"

User: "Fill in the username field with 'admin'"
Emma: Uses browser_fill with selector "#username" and value "admin"

User: "Scroll down the page"
Emma: Uses browser_scroll with pixels 500

User: "Go back to the previous page"
Emma: Uses browser_go_back
```

### Real-time Monitoring

```javascript
// Live desktop viewer
const ws = new WebSocket('ws://localhost:8000/api/live/desktop/stream');
const img = document.getElementById('desktop-view');

ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  if (data.type === 'frame') {
    img.src = `data:image/png;base64,${data.image}`;
  }
};

// Interactive controls
document.getElementById('click-btn').addEventListener('click', async () => {
  await fetch('/api/live/desktop/mouse/click', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({x: 500, y: 300, button: 'left'})
  });
});
```

## 🚀 Getting Started

1. **Install dependencies:**
   ```bash
   pip install 'emma-ai[desktop]'
   pip install playwright
   playwright install chromium
   ```

2. **Restart Emma** to activate the new capabilities

3. **Test live streaming:**
   ```bash
   # Test screenshot
   curl http://localhost:8000/api/live/desktop/screenshot --output test.png
   ```

4. **Test browser interaction:**
   ```bash
   # Open a page first
   curl -X POST http://localhost:8000/api/chat \
     -H "Content-Type: application/json" \
     -d '{"message": "Open https://example.com in browser"}'
   ```

## 📊 Performance

- **Desktop streaming:** ~2 FPS (adjustable)
- **Screenshot capture:** <100ms
- **Mouse operations:** <50ms
- **Keyboard operations:** <50ms
- **Browser actions:** <200ms (depends on page load)

## 🔧 Troubleshooting

**Desktop streaming not working:**
- Ensure pyautogui is installed: `pip install pyautogui`
- Check desktop permissions
- Verify screen capture is allowed

**Browser interaction not working:**
- Ensure playwright is installed: `pip install playwright`
- Install chromium: `playwright install chromium`
- Check network gate is open for web operations

**Consent required:**
- Some actions require user approval
- Check Guardian settings in `/api/system/status`
- Approve pending consents via `/api/security/consent`

## 📝 Notes

- Live streaming is resource-intensive (CPU usage)
- Consider frame rate adjustment for performance
- Browser automation requires network access
- All actions are audited for security
- Some operations may require user consent
