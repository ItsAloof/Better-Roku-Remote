# Better Roku Remote

A fast, lightweight Roku remote that runs in your phone's browser. A small Python server on your computer finds your Rokus and sends button presses straight to them over your home network, using Roku's External Control Protocol (ECP). There's no cloud in between, so presses register instantly.

## Features

- **Buttons mode:** a giant radial D-pad you can use without looking. The whole pad counts, corners included.
- **Swipe mode:** swipe to move, tap to select, and hold after a swipe to keep scrolling.
- **All the usual buttons:** Power, Back, Options, Home, Instant Replay, Mute, Volume, Rewind, Play/Pause and Fast-forward.
- **Apps:** your full app list in the order you arranged it on the Roku, with search and recently launched apps.
- **Now Playing:** shows the open app and, when the app reports it, how far into the show you are.
- **Search:** opens Roku's search results on the TV, across all your streaming apps.
- **Keyboard:** live typing into text boxes on the TV.
- **Multiple Rokus:** finds them automatically. Rename them, put them in your own order, and star a default that the remote always opens on. Each Roku is tracked by its serial number, so if your router gives it a new IP address the remote never ends up controlling the wrong one.
- **Feel settings:** hold-to-repeat speed and swipe sensitivity, in Settings → Controls.
- **Seasonal themes with animations,** set up in `config.json`, plus one hidden theme you'll have to find in the app 🤫
- **Haptic feedback** on Android, and on iOS 18+ through a workaround.

## Requirements

- Python 3.8 or newer, standard library only, so there's nothing to install.
- Your computer and phone on the same Wi-Fi network as the Roku.
- On the Roku: **Settings → System → Advanced system settings → Control by mobile apps → Network access** set to **Enabled** (recommended). In **Limited** mode the Roku blocks everything except basic buttons, so the D-pad and media keys still work, but Apps, Now Playing and the Roku's own name don't.

## Run it

Double-click the start script for your computer:

| System | File |
|---|---|
| Windows | `start.bat` |
| macOS | `start.command` (the first time, right-click → **Open** to get past the "unidentified developer" warning) |
| Linux | `start.sh` |

Or run it yourself with `python3 roku_remote.py`.

**On Windows, the first time it runs** a firewall prompt asks whether Python can use the network. Allow it on **Private networks**, or your phone won't be able to connect.

Then open the address it prints, like `http://192.168.x.x:8080`, on your phone. On iPhone, tap Share → **Add to Home Screen** so it opens full-screen like an app.

To use a different port, set `PORT`:

```bash
PORT=9000 python3 roku_remote.py
```

Keep `index.html`, `config.json` and the `icons` folder in the same folder as `roku_remote.py`.

## On a computer

Opening the remote on a computer automatically shows the **desktop layout** at `/desktop`:

- A Roku-style home screen with your real app icons, in the order you arranged them on the Roku, plus your recently opened apps. Click an app to open it on the selected Roku.
- A remote panel you can put on the **right**, on the **left**, **floating**, or **hidden** (Settings → Remote, or press `R`).
- The header has the Roku picker (★ sets the default), Now Playing, Search on your TV, and Power.
- Keyboard control: arrow keys move, `Enter` is OK, `Backspace`/`Esc` is Back, `H` is Home, `Space` is Play/Pause, `,` `.` rewind/forward, `-` `+` change volume, `M` mutes, `*` is Options, `/` jumps to search. You can turn these off in Settings.
- The same themes and animations as the phone, with app tiles that animate in and glow on hover.

Settings, Roku order, names and the default are shared between the phone and desktop layouts. To switch layouts, use the link at the bottom of Settings in either one.

## Always on (start automatically)

So the remote works whenever you pick up your phone, without starting anything. Run the autostart script once. Running it again turns autostart off.

| System | File | What it does |
|---|---|---|
| Windows | `autostart-windows.bat` | Starts the remote hidden in the background each time you log in |
| macOS | `autostart-mac.command` | Runs it in the background at login and restarts it if it ever stops |
| Linux / Raspberry Pi | `autostart-linux.sh` | Runs it as a background service that starts at boot and restarts if it stops |

A **Raspberry Pi** works well as an always-on home for the remote: it uses very little power and never sleeps. Copy the folder over and run `./autostart-linux.sh`.

Your computer has to stay awake for the remote to work. If it sleeps, the remote is unreachable until it wakes up.

## Show search (experimental branch)

On the `experimental-search` branch, **Search** finds movies and shows on your phone. Results show posters, ratings and which streaming services carry each title, and tapping **Open** next to a service you have installed opens that app on your Roku. The remote can't start the show itself, because Roku keeps that ability to its own app, so after it opens, use **Keyboard** to search inside the app.

Setup:

1. Get a free API key from [themoviedb.org](https://www.themoviedb.org/settings/api). Either the "API Key" or the "API Read Access Token" works.
2. Copy `secrets.example.json` to `secrets.json` and paste the key in. `secrets.json` is ignored by git, so your key never ends up on GitHub.
3. Restart the server.

`tmdb_region` in `config.json` sets which country's streaming services are listed. It defaults to `US`. Without a key, Search falls back to Roku's own search on the TV.

This product uses the TMDB API but is not endorsed or certified by TMDB. Streaming availability data is provided by JustWatch.

## Virtual Roku (for testing)

Open the remote's address with `?fake-roku` on the end, like `http://192.168.x.x:8080/?fake-roku`, on your computer or any screen. It shows a pretend Roku TV with a home screen of apps, app pages, a video player, search and volume. While that page is open, **Virtual Roku** appears in the remote's device list, and every button, the keyboard, app launching and Now Playing work against it. It's handy for trying changes without touching your real TV. Close the page and it disappears from the list within about 30 seconds.

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
- `secret`: `true` hides the theme from the picker until it's unlocked in the app.
- `effect`: `"prism"` adds the liquid RGB background, glass buttons and tap bursts.
- `particles`: `null` for none, or:
  - `chars`: emoji or symbols
  - `motion`: `fall`, `rise` or `twinkle`
  - `count`, `size` (`[min, max]` in px), `speed`, `opacity`
  - `palette`: colors for plain symbols; emoji keep their own colors

## Troubleshooting

- **"Can't reach the remote server":** make sure the script is running, and that your phone is on the same network as the computer.
- **"Roku blocked it" or presses don't work:** check the Control by mobile apps setting above. Also turn off any VPN on the computer.
- **"Port 8080 is already in use":** the remote is already running, probably in the background from autostart. Nothing else to do.
- **No Rokus found:** tap the device name, then **Add by IP**. You'll find the Roku's IP under **Settings → Network → About**.
- **Volume does nothing:** volume only works on Roku TVs, or when the TV supports HDMI-CEC.

## How it works

`roku_remote.py` finds Rokus with SSDP, falling back to scanning your subnet. It serves the remote page and forwards each press to `http://<roku-ip>:8060`, sending presses to each Roku one at a time so they arrive in order. Phone browsers can't do device discovery themselves, which is why the small server is needed.
