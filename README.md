# Better Roku Remote

A fast, lightweight Roku remote that runs in your phone's browser. A small Python server on your computer finds your Rokus and sends button presses straight to them over your home network, using Roku's External Control Protocol (ECP). There's no cloud in between, so presses register instantly.

## Features

- **Buttons mode:** a giant radial D-pad you can use without looking. The whole pad counts, corners included.
- **Swipe mode:** swipe to move, tap to select, and hold after a swipe to keep scrolling.
- **All the usual buttons:** Power, Back, Options, Home, Instant Replay, Mute, Volume, Rewind, Play/Pause and Fast-forward.
- **Apps:** your full app list in the order you arranged it on the Roku, with search and recently launched apps.
- **Search:** opens Roku's search results on the TV, across all your streaming apps.
- **Keyboard:** live typing into text boxes on the TV.
- **Multiple Rokus:** finds them automatically, and you can rename them on your phone.
- **Seasonal themes with animations,** set up in `config.json`.
- **Haptic feedback** on Android, and on iOS 18+ through a workaround.

## Requirements

- Python 3.8 or newer, standard library only, so there's nothing to install.
- Your computer and phone on the same Wi-Fi network as the Roku.
- On the Roku: **Settings → System → Advanced system settings → Control by mobile apps → Network access** set to **Enabled** or **Permissive**. In **Limited** mode the server can't identify the Roku, so the remote can't send anything.

## Run it

```bash
python3 roku_remote.py
```

Then open the address it prints, like `http://192.168.x.x:8080`, on your phone. On iPhone, tap Share → **Add to Home Screen** so it opens full-screen like an app.

To use a different port, set `PORT`:

```bash
PORT=9000 python3 roku_remote.py
```

Keep `index.html` and `config.json` in the same folder as `roku_remote.py`.

## Themes (`config.json`)

The file is re-read every time the remote opens, so you don't need to restart the server after editing it.

| Setting | What it does |
|---|---|
| `theme` | `"auto"` picks a theme by today's date, or name one, such as `"christmas"` |
| `animations` | Default animation setting for phones that haven't changed it in Settings |
| `lock_theme` | `true` hides the theme picker so every phone uses the theme set here |

**To add a theme,** copy an existing block in `themes`, give it a new key and `label`, and change:

- `dates`: `["MM-DD", "MM-DD"]`. A range can wrap the new year, like `["12-30", "01-02"]`.
- `colors`: `bg`, `glow`, `pad-top`, `pad-bot`, `pad-hi`, `ok`, `ok-ring`, `gap`, `sheet`. Any you leave out keep the default.
- `particles`: `null` for none, or:
  - `chars`: emoji or symbols
  - `motion`: `fall`, `rise` or `twinkle`
  - `count`, `size` (`[min, max]` in px), `speed`, `opacity`
  - `palette`: colors for plain symbols; emoji keep their own colors

## Troubleshooting

- **"Can't reach the remote server":** make sure the script is running, and that your phone is on the same network as the computer.
- **"Roku blocked it" or presses don't work:** check the Control by mobile apps setting above. Also turn off any VPN on the computer.
- **No Rokus found:** tap the device name, then **Add by IP**. You'll find the Roku's IP under **Settings → Network → About**.
- **Volume does nothing:** volume only works on Roku TVs, or when the TV supports HDMI-CEC.

## How it works

`roku_remote.py` finds Rokus with SSDP, falling back to scanning your subnet. It serves the remote page and forwards each press to `http://<roku-ip>:8060`, sending presses to each Roku one at a time so they arrive in order. Phone browsers can't do device discovery themselves, which is why the small server is needed.
