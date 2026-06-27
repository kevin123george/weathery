# weathery

Terminal weather forecast TUI — built with [Textual](https://github.com/Textualize/textual) and [Open-Meteo](https://open-meteo.com) (free, no API key needed).

## Features

- Current conditions: temperature, feels like, humidity, wind, UV index, pressure, visibility
- 24h hourly temperature + precipitation probability chart
- 7-day forecast table
- Detailed sun/UV/atmosphere panel
- Multiple saved locations
- Search any city worldwide
- Toggle °C / °F
- Auto-refreshes every 10 minutes
- Data persisted in `~/.weathery/`

## Install

```sh
pipx install git+https://github.com/kevin123george/weathery.git
```

Then run:

```sh
weathery
```

## Keybindings

| Key | Action |
|-----|--------|
| `j` / `k` | Move up/down location list |
| `a` | Add location (search by city name) |
| `d` | Delete current location |
| `u` | Toggle °C / °F |
| `r` | Force refresh |
| `q` | Quit |

## Data

Locations saved to `~/.weathery/locations.json`. Weather data from [Open-Meteo](https://open-meteo.com) — free and open source, no API key required.

## License

MIT
