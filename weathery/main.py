#!/usr/bin/env python3
"""weathery — terminal weather forecast TUI"""

import sys, threading, json, time, io
from datetime import datetime
from pathlib import Path
from urllib.request import urlopen
from urllib.parse import urlencode
from urllib.error import URLError
from rich.ansi import AnsiDecoder

import plotext as plt
from textual.app import App, ComposeResult
from textual.widgets import (
    Static, ListView, ListItem, Label,
    Footer, Header, Input, DataTable,
    TabbedContent, TabPane, Button,
)
from textual.containers import Horizontal, Vertical, ScrollableContainer
from textual.binding import Binding
from textual.screen import ModalScreen

_ansi = AnsiDecoder()

# ── Paths ──────────────────────────────────────────────────────────────────────
DATA_DIR = Path.home() / ".weathery"
DATA_DIR.mkdir(exist_ok=True)
LOC_FILE = DATA_DIR / "locations.json"

DEFAULT_LOCATIONS = [
    {"name": "New York",    "lat": 40.7128,  "lon": -74.0060,  "tz": "America/New_York"},
    {"name": "London",      "lat": 51.5074,  "lon": -0.1278,   "tz": "Europe/London"},
    {"name": "Tokyo",       "lat": 35.6762,  "lon": 139.6503,  "tz": "Asia/Tokyo"},
    {"name": "Sydney",      "lat": -33.8688, "lon": 151.2093,  "tz": "Australia/Sydney"},
    {"name": "Los Angeles", "lat": 34.0522,  "lon": -118.2437, "tz": "America/Los_Angeles"},
]

# WMO weather interpretation codes
WMO = {
    0: ("Clear Sky", "☀️"),
    1: ("Mainly Clear", "🌤"),
    2: ("Partly Cloudy", "⛅"),
    3: ("Overcast", "☁️"),
    45: ("Fog", "🌫"),
    48: ("Icy Fog", "🌫"),
    51: ("Light Drizzle", "🌦"),
    53: ("Drizzle", "🌦"),
    55: ("Heavy Drizzle", "🌧"),
    61: ("Light Rain", "🌧"),
    63: ("Rain", "🌧"),
    65: ("Heavy Rain", "🌧"),
    71: ("Light Snow", "🌨"),
    73: ("Snow", "❄️"),
    75: ("Heavy Snow", "❄️"),
    77: ("Snow Grains", "❄️"),
    80: ("Light Showers", "🌦"),
    81: ("Showers", "🌧"),
    82: ("Heavy Showers", "⛈"),
    85: ("Snow Showers", "🌨"),
    86: ("Heavy Snow Showers", "❄️"),
    95: ("Thunderstorm", "⛈"),
    96: ("Thunderstorm+Hail", "⛈"),
    99: ("Thunderstorm+Hail", "⛈"),
}

WIND_DIR = ["N","NNE","NE","ENE","E","ESE","SE","SSE",
            "S","SSW","SW","WSW","W","WNW","NW","NNW"]

def _wmo_label(code):
    code = int(code) if code is not None else 0
    info = WMO.get(code, ("Unknown", "?"))
    return info[0], info[1]

def _wind_dir(deg):
    if deg is None: return "N/A"
    return WIND_DIR[round(float(deg) / 22.5) % 16]

def _feels_like_desc(temp, feels):
    if temp is None or feels is None: return ""
    diff = float(feels) - float(temp)
    if diff <= -5: return "feels much colder"
    if diff <= -2: return "feels colder"
    if diff >=  5: return "feels much hotter"
    if diff >=  2: return "feels hotter"
    return "feels similar"

# ── Persistence ────────────────────────────────────────────────────────────────
def _load(p, default):
    try:    return json.loads(Path(p).read_text())
    except: return default

def _save(p, data): Path(p).write_text(json.dumps(data, indent=2))

# ── API ────────────────────────────────────────────────────────────────────────
def _fetch(url, timeout=10):
    with urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())

def geocode(name: str):
    """Search for a location by name, return list of matches."""
    params = urlencode({"name": name, "count": 5, "language": "en", "format": "json"})
    url = f"https://geocoding-api.open-meteo.com/v1/search?{params}"
    data = _fetch(url)
    results = data.get("results", [])
    out = []
    for r in results:
        label = r.get("name","")
        admin = r.get("admin1","")
        country = r.get("country","")
        if admin: label += f", {admin}"
        if country: label += f" ({country})"
        out.append({
            "name": label,
            "lat":  r["latitude"],
            "lon":  r["longitude"],
            "tz":   r.get("timezone", "UTC"),
        })
    return out

def fetch_weather(lat, lon, tz="UTC", unit="celsius"):
    params = urlencode({
        "latitude":  lat,
        "longitude": lon,
        "timezone":  tz,
        "temperature_unit": unit,
        "wind_speed_unit": "kmh",
        "current": ",".join([
            "temperature_2m","relative_humidity_2m","apparent_temperature",
            "is_day","precipitation","weather_code","cloud_cover",
            "wind_speed_10m","wind_direction_10m","wind_gusts_10m",
            "surface_pressure","uv_index","visibility",
        ]),
        "hourly": ",".join([
            "temperature_2m","apparent_temperature","precipitation_probability",
            "precipitation","weather_code","wind_speed_10m","uv_index",
        ]),
        "daily": ",".join([
            "weather_code","temperature_2m_max","temperature_2m_min",
            "apparent_temperature_max","apparent_temperature_min",
            "sunrise","sunset","precipitation_sum","precipitation_probability_max",
            "wind_speed_10m_max","uv_index_max",
        ]),
        "forecast_days": 16,
    })
    url = f"https://api.open-meteo.com/v1/forecast?{params}"
    return _fetch(url)

# ── plotext helper ─────────────────────────────────────────────────────────────
def _plt_build():
    try:
        return plt.build()
    except AttributeError:
        import sys as _sys
        old = _sys.stdout; _sys.stdout = buf = io.StringIO()
        plt.show(); _sys.stdout = old
        return buf.getvalue()

# ── Modals ─────────────────────────────────────────────────────────────────────
class SearchModal(ModalScreen):
    """Two-step modal: type city → Enter to search → press 1-5 to pick result."""
    CSS = """
    SearchModal { align: center middle; }
    #box { width: 64; padding: 1 2; border: solid #89b4fa; background: #313244; }
    #title  { color: #89b4fa; margin-bottom: 1; }
    Input   { background: #45475a; color: #cdd6f4; border: solid #585b70; margin-bottom: 1; }
    #list   { height: 7; color: #cdd6f4; margin-bottom: 1; }
    #hint   { color: #6c7086; }
    """

    def __init__(self):
        super().__init__()
        self._results = []

    def compose(self) -> ComposeResult:
        with Vertical(id="box"):
            yield Static("Search location", id="title")
            yield Input(placeholder="City name  e.g. Hannover", id="inp")
            yield Static("", id="list")
            yield Static("Enter city name and press Enter", id="hint")

    def on_mount(self):
        self.query_one("#inp", Input).focus()

    def on_input_submitted(self, e: Input.Submitted):
        q = e.value.strip()
        if not q: return
        self.query_one("#hint").update("[#f9e2af]Searching…[/#f9e2af]")
        self.query_one("#list").update("")
        self._results = []
        threading.Thread(target=self._search, args=(q,), daemon=True).start()

    def _search(self, q):
        try:
            results = geocode(q)
        except Exception as exc:
            self.app.call_from_thread(self._show, [], f"[red]Error: {exc}[/red]")
            return
        self.app.call_from_thread(self._show, results,
            "[#a6e3a1]Press 1–5 to add  •  Esc to cancel[/#a6e3a1]" if results
            else "[red]No results found — try a different spelling[/red]")

    def _show(self, results, hint):
        self._results = results
        lines = []
        for i, r in enumerate(results[:5], 1):
            lines.append(f" [bold #89b4fa]{i}[/bold #89b4fa]  {r['name']}")
        self.query_one("#list").update("\n".join(lines))
        self.query_one("#hint").update(hint)
        # Disable Input so it stops consuming keypresses — 1-5 will reach on_key
        self.query_one("#inp", Input).disabled = True

    def on_key(self, e):
        if e.key == "escape":
            self.dismiss(None)
        elif e.key in ("1","2","3","4","5") and self._results:
            idx = int(e.key) - 1
            if idx < len(self._results):
                self.dismiss(self._results[idx])


# ── Main App ───────────────────────────────────────────────────────────────────
class WeatherApp(App):
    CSS = """
    Screen { background: #1e1e2e; }
    Header { background: #181825; color: #cdd6f4; }
    Footer { background: #181825; color: #585b70; }
    #main  { height: 1fr; }
    #left  { width: 26; border-right: solid #313244; }
    #loc-hdr { height: 1; background: #313244; color: #89b4fa; padding: 0 1; }
    ListView { background: #1e1e2e; border: none; }
    ListItem { background: #1e1e2e; color: #cdd6f4; padding: 0 1; height: 1; }
    ListItem:hover { background: #313244; }
    ListItem.--highlight { background: #45475a; color: #89b4fa; }
    #right { width: 1fr; }
    #loc-line   { height: 1; margin-top: 1; padding: 0 2; color: #89b4fa; }
    #temp-line  { height: 2; padding: 0 2; }
    TabbedContent { height: 1fr; }
    TabPane { padding: 0 1; }
    DataTable { height: 1fr; }
    #hourly-area { height: 1fr; }
    #detail-sc   { height: 1fr; }
    #status { height: 1; padding: 0 2; color: #585b70; }
    """

    BINDINGS = [
        Binding("q", "quit",         "Quit",     priority=True),
        Binding("a", "add_location", "Add",      priority=True),
        Binding("d", "del_location", "Del",      priority=True),
        Binding("r", "refresh",      "Refresh",  priority=True),
        Binding("u", "toggle_unit",  "°C/°F",   priority=True),
        Binding("j", "cursor_down",  "↓", show=False),
        Binding("k", "cursor_up",    "↑", show=False),
    ]

    REFRESH_INTERVAL = 600  # 10 minutes — weather doesn't change every second

    def __init__(self):
        super().__init__()
        self._locations   = _load(LOC_FILE, DEFAULT_LOCATIONS)
        self._cur_idx     = 0
        self._unit        = "celsius"   # or "fahrenheit"
        self._weather     = {}          # loc name → raw API response
        self._lock        = threading.Lock()

    @property
    def _cur_loc(self): return self._locations[self._cur_idx]

    # ── Compose ────────────────────────────────────────────────────────────────
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="main"):
            with Vertical(id="left"):
                yield Static("  Locations   a=add  d=del", id="loc-hdr")
                yield ListView(id="loc-list")
            with Vertical(id="right"):
                yield Static("", id="loc-line")
                yield Static("", id="temp-line")
                with TabbedContent(id="tabs"):
                    with TabPane("Now", id="tab-now"):
                        yield Static("", id="now-content")
                    with TabPane("Hourly", id="tab-hourly"):
                        yield Static("", id="hourly-area")
                    with TabPane("16-Day", id="tab-weekly"):
                        yield DataTable(id="weekly-tbl", zebra_stripes=True)
                    with TabPane("Details", id="tab-details"):
                        with ScrollableContainer(id="detail-sc"):
                            yield Static("", id="detail-content")
                yield Static("", id="status")
        yield Footer()

    def on_mount(self):
        self._init_tables()
        self._rebuild_list()
        self._status("Fetching weather…")
        threading.Thread(target=self._boot, daemon=True).start()
        self.set_interval(self.REFRESH_INTERVAL, self._auto_refresh)

    def _init_tables(self):
        t = self.query_one("#weekly-tbl", DataTable)
        t.add_columns("Day", "Cond", "High", "Low", "Rain%", "Rain", "Wind", "UV")

    # ── Data loading ───────────────────────────────────────────────────────────
    def _boot(self):
        for loc in self._locations:
            self._fetch_loc(loc)

    def _fetch_loc(self, loc: dict):
        try:
            data = fetch_weather(loc["lat"], loc["lon"], loc.get("tz","UTC"), self._unit)
            with self._lock:
                self._weather[loc["name"]] = data
            self.call_from_thread(self._draw_list_item, loc["name"])
            if loc["name"] == self._cur_loc["name"]:
                self.call_from_thread(self._draw_all)
        except Exception as exc:
            self.call_from_thread(self._status, f"Error fetching {loc['name']}: {exc}")

    def _auto_refresh(self):
        self._status("Refreshing…")
        threading.Thread(target=self._boot, daemon=True).start()

    # ── Unit helpers ───────────────────────────────────────────────────────────
    def _deg(self):   return "°F" if self._unit == "fahrenheit" else "°C"
    def _speed(self): return "km/h"

    def _fmt_temp(self, v):
        if v is None: return "N/A"
        return f"{float(v):.1f}{self._deg()}"

    # ── Draw ───────────────────────────────────────────────────────────────────
    def _rebuild_list(self):
        lv = self.query_one("#loc-list", ListView)
        for _ in range(len(list(lv.query(ListItem)))):
            lv.pop(0)
        for loc in self._locations:
            lv.append(ListItem(Label(self._list_label(loc))))

    def _list_label(self, loc: dict) -> str:
        name = loc["name"]
        short = name.split(",")[0][:18]
        data = self._weather.get(name)
        if not data:
            return f"{short}"
        c = data.get("current", {})
        temp = c.get("temperature_2m")
        code = c.get("weather_code", 0)
        _, icon = _wmo_label(code)
        if temp is None: return short
        return f"{short:<18} {float(temp):.0f}{self._deg()}"

    def _draw_list_item(self, name: str):
        idx = next((i for i,l in enumerate(self._locations) if l["name"]==name), None)
        if idx is None: return
        items = list(self.query_one("#loc-list", ListView).query(ListItem))
        if idx < len(items):
            items[idx].query_one(Label).update(
                self._list_label(self._locations[idx]))

    def _draw_all(self):
        name = self._cur_loc["name"]
        data = self._weather.get(name)
        self.query_one("#loc-line").update(
            f" [bold]{name}[/bold]   "
            f"[#6c7086]{self._cur_loc.get('tz','')}[/#6c7086]   "
            f"[#585b70]{self._deg()}  u=toggle units[/#585b70]")
        if not data:
            self.query_one("#temp-line").update(" [#6c7086]Loading…[/#6c7086]")
            return
        self._draw_now(data)
        self._draw_hourly(data)
        self._draw_weekly(data)
        self._draw_details(data)
        now = datetime.now().strftime("%H:%M:%S")
        self._status(f"Updated {now}")

    def _draw_now(self, data: dict):
        c    = data.get("current", {})
        temp = c.get("temperature_2m")
        feel = c.get("apparent_temperature")
        code = c.get("weather_code", 0)
        desc, icon = _wmo_label(code)
        hum  = c.get("relative_humidity_2m")
        wind = c.get("wind_speed_10m")
        wdir = _wind_dir(c.get("wind_direction_10m"))
        gust = c.get("wind_gusts_10m")
        prec = c.get("precipitation", 0)
        uv   = c.get("uv_index")
        vis  = c.get("visibility")
        pres = c.get("surface_pressure")
        cld  = c.get("cloud_cover")
        is_day = c.get("is_day", 1)

        feel_desc = _feels_like_desc(temp, feel)
        day_night = "[bold #f9e2af]DAY[/bold #f9e2af]" if is_day \
                    else "[bold #89b4fa]NIGHT[/bold #89b4fa]"

        self.query_one("#temp-line").update(
            f" [bold #cdd6f4]{self._fmt_temp(temp)}[/bold #cdd6f4]"
            f"  [#a6e3a1]{desc}[/#a6e3a1]"
            f"  {day_night}"
            f"  [#6c7086]feels {self._fmt_temp(feel)}  {feel_desc}[/#6c7086]"
        )

        R = 18
        vis_km = f"{float(vis)/1000:.1f} km" if vis else "N/A"
        uv_clr = "green" if (uv or 0) < 3 else "yellow" if (uv or 0) < 6 \
                 else "orange1" if (uv or 0) < 8 else "red"
        uv_str = f"[{uv_clr}]{uv:.1f}[/{uv_clr}]" if uv is not None else "N/A"

        self.query_one("#now-content").update(
            f"[bold #89b4fa]── Conditions {'─'*20}[/bold #89b4fa]"
            f"   [bold #89b4fa]── Wind {'─'*26}[/bold #89b4fa]\n"
            f"{'Temperature':<{R}}{self._fmt_temp(temp)}"
            f"   {'Wind Speed':<{R}}{wind:.0f} {self._speed()}" if wind else
            f"{'Temperature':<{R}}{self._fmt_temp(temp)}"
            f"   {'Wind Speed':<{R}}N/A"
            + f"\n{'Feels Like':<{R}}{self._fmt_temp(feel)}"
            f"   {'Direction':<{R}}{wdir}  {c.get('wind_direction_10m',0):.0f}°\n"
            f"{'Humidity':<{R}}{hum}%" if hum else
            f"{'Humidity':<{R}}N/A"
            + f"\n{'Precipitation':<{R}}{prec:.1f} mm"
            f"   {'Gusts':<{R}}{gust:.0f} {self._speed()}\n" if gust else
            f"\n{'Precipitation':<{R}}{prec:.1f} mm"
            f"   {'Gusts':<{R}}N/A\n"
            + f"{'Cloud Cover':<{R}}{cld}%" if cld is not None else
            f"{'Cloud Cover':<{R}}N/A"
            + f"   [bold #89b4fa]── Atmosphere {'─'*21}[/bold #89b4fa]\n"
            f"{'Visibility':<{R}}{vis_km}"
            f"   {'Pressure':<{R}}{pres:.0f} hPa\n" if pres else
            f"{'Visibility':<{R}}{vis_km}"
            f"   {'Pressure':<{R}}N/A\n"
            + f"{'UV Index':<{R}}{uv_str}"
            f"   {'Condition Code':<{R}}{int(code)}"
        )

    def _draw_now(self, data: dict):
        c    = data.get("current", {})
        temp = c.get("temperature_2m")
        feel = c.get("apparent_temperature")
        code = c.get("weather_code", 0)
        desc, icon = _wmo_label(code)
        hum  = c.get("relative_humidity_2m")
        wind = c.get("wind_speed_10m")
        wdir = _wind_dir(c.get("wind_direction_10m"))
        gust = c.get("wind_gusts_10m")
        prec = c.get("precipitation", 0) or 0
        uv   = c.get("uv_index")
        vis  = c.get("visibility")
        pres = c.get("surface_pressure")
        cld  = c.get("cloud_cover")
        is_day = c.get("is_day", 1)

        feel_desc = _feels_like_desc(temp, feel)
        day_night = "[bold #f9e2af]DAY[/bold #f9e2af]" if is_day \
                    else "[bold #89b4fa]NIGHT[/bold #89b4fa]"

        self.query_one("#temp-line").update(
            f" [bold #cdd6f4]{self._fmt_temp(temp)}[/bold #cdd6f4]"
            f"  [#a6e3a1]{desc}[/#a6e3a1]"
            f"  {day_night}"
            f"  [#6c7086]feels {self._fmt_temp(feel)}  {feel_desc}[/#6c7086]"
        )

        R = 20
        vis_km = f"{float(vis)/1000:.1f} km" if vis else "N/A"
        uv_clr = "green" if (uv or 0) < 3 else "yellow" if (uv or 0) < 6 \
                 else "orange1" if (uv or 0) < 8 else "red"
        uv_str = f"[{uv_clr}]{float(uv):.1f}[/{uv_clr}]" if uv is not None else "N/A"
        wind_str  = f"{float(wind):.0f} {self._speed()}" if wind is not None else "N/A"
        gust_str  = f"{float(gust):.0f} {self._speed()}" if gust is not None else "N/A"
        hum_str   = f"{hum}%" if hum is not None else "N/A"
        cld_str   = f"{cld}%" if cld is not None else "N/A"
        pres_str  = f"{float(pres):.0f} hPa" if pres is not None else "N/A"
        wdeg_str  = f"{wdir}  {float(c.get('wind_direction_10m',0)):.0f}°"

        self.query_one("#now-content").update(
            f"[bold #89b4fa]── Conditions {'─'*18}[/bold #89b4fa]"
            f"   [bold #89b4fa]── Wind & Atmosphere {'─'*12}[/bold #89b4fa]\n"
            f"{'Temperature':<{R}}{self._fmt_temp(temp)}"
            f"   {'Wind Speed':<{R}}{wind_str}\n"
            f"{'Feels Like':<{R}}{self._fmt_temp(feel)}"
            f"   {'Direction':<{R}}{wdeg_str}\n"
            f"{'Humidity':<{R}}{hum_str}"
            f"   {'Gusts':<{R}}{gust_str}\n"
            f"{'Precipitation':<{R}}{float(prec):.1f} mm"
            f"   {'Pressure':<{R}}{pres_str}\n"
            f"{'Cloud Cover':<{R}}{cld_str}"
            f"   {'Visibility':<{R}}{vis_km}\n"
            f"{'UV Index':<{R}}{uv_str}"
            f"   {'Condition':<{R}}{desc}\n"
        )

    def _draw_hourly(self, data: dict):
        h    = data.get("hourly", {})
        times = h.get("time", [])
        temps = h.get("temperature_2m", [])
        feels = h.get("apparent_temperature", [])
        probs = h.get("precipitation_probability", [])
        precs = h.get("precipitation", [])

        area = self.query_one("#hourly-area")
        if not times or not temps:
            area.update("No hourly data"); return

        # Show next 24 hours
        now_str = datetime.now().strftime("%Y-%m-%dT%H:00")
        start = 0
        for i, t in enumerate(times):
            if t >= now_str: start = i; break

        end   = min(start + 24, len(temps))
        t_slice = temps[start:end]
        p_slice = probs[start:end] if probs else [0]*24
        times_s = [t[11:16] for t in times[start:end]]  # HH:MM

        try:
            w = max(area.size.width  or 100, 60)
            h_sz = max(area.size.height or 24, 14)

            plt.clf()
            plt.subplots(2, 1)

            plt.subplot(1, 1)
            plt.plotsize(w, int(h_sz * 0.65))
            plt.plot(t_slice, label=f"Temp ({self._deg()})")
            if feels:
                f_slice = feels[start:end]
                plt.plot(f_slice, label="Feels like")
            plt.title(f"24h Temperature  ({self._deg()})")
            plt.xticks(list(range(0, len(times_s), 3)), times_s[::3])

            plt.subplot(2, 1)
            plt.plotsize(w, int(h_sz * 0.35))
            plt.bar(list(range(len(p_slice))), p_slice)
            plt.title("Precip Probability (%)")
            plt.xticks(list(range(0, len(times_s), 3)), times_s[::3])
            plt.ylim(0, 100)

            chart_str = _plt_build()
            area.update("\n".join(str(l) for l in _ansi.decode(chart_str)))
        except Exception as exc:
            area.update(f"Chart error: {exc}")

    def _draw_weekly(self, data: dict):
        d   = data.get("daily", {})
        tbl = self.query_one("#weekly-tbl", DataTable)
        tbl.clear()
        dates   = d.get("time", [])
        codes   = d.get("weather_code", [])
        t_max   = d.get("temperature_2m_max", [])
        t_min   = d.get("temperature_2m_min", [])
        r_prob  = d.get("precipitation_probability_max", [])
        r_sum   = d.get("precipitation_sum", [])
        wind    = d.get("wind_speed_10m_max", [])
        uv      = d.get("uv_index_max", [])

        today = datetime.now().strftime("%Y-%m-%d")
        for i, date in enumerate(dates):
            try:
                dt = datetime.strptime(date, "%Y-%m-%d")
                day = "Today" if date == today else dt.strftime("%a %d %b")
            except: day = date
            code  = codes[i] if i < len(codes) else 0
            desc, _ = _wmo_label(code)
            hi  = self._fmt_temp(t_max[i])  if i < len(t_max)  else "N/A"
            lo  = self._fmt_temp(t_min[i])  if i < len(t_min)  else "N/A"
            rp  = f"{r_prob[i]:.0f}%"       if i < len(r_prob) and r_prob[i] is not None else "N/A"
            rs  = f"{float(r_sum[i]):.1f}mm" if i < len(r_sum) and r_sum[i] is not None  else "0mm"
            ws  = f"{float(wind[i]):.0f} {self._speed()}" if i < len(wind) and wind[i] is not None else "N/A"
            uv_v = float(uv[i]) if i < len(uv) and uv[i] is not None else 0
            uv_clr = "green" if uv_v < 3 else "yellow" if uv_v < 6 else "red"
            uv_s = f"[{uv_clr}]{uv_v:.0f}[/{uv_clr}]"
            tbl.add_row(day, desc, hi, lo, rp, rs, ws, uv_s)

    def _draw_details(self, data: dict):
        d = data.get("daily", {})
        c = data.get("current", {})
        sunrises = d.get("sunrise", [])
        sunsets  = d.get("sunset", [])
        sr = sunrises[0][11:16] if sunrises and sunrises[0] else "N/A"
        ss = sunsets[0][11:16]  if sunsets  and sunsets[0]  else "N/A"

        hourly = data.get("hourly", {})
        h_times = hourly.get("time", [])
        h_uv    = hourly.get("uv_index", [])
        now_str = datetime.now().strftime("%Y-%m-%dT%H:00")
        cur_uv  = None
        for i, t in enumerate(h_times):
            if t >= now_str and i < len(h_uv):
                cur_uv = h_uv[i]; break

        uv_desc = ""
        uv_v = float(cur_uv) if cur_uv is not None else float(c.get("uv_index") or 0)
        if uv_v < 3:   uv_desc = "Low — no protection needed"
        elif uv_v < 6: uv_desc = "Moderate — protection recommended"
        elif uv_v < 8: uv_desc = "High — protection essential"
        elif uv_v < 11: uv_desc = "Very High — extra protection"
        else:           uv_desc = "Extreme — stay indoors midday"

        R = 22
        self.query_one("#detail-content").update(
            f"[bold #89b4fa]── Sun & Daylight ──────────────────────[/bold #89b4fa]\n"
            f"{'Sunrise':<{R}}{sr}\n"
            f"{'Sunset':<{R}}{ss}\n\n"
            f"[bold #89b4fa]── UV Index ────────────────────────────[/bold #89b4fa]\n"
            f"{'UV Index':<{R}}{uv_v:.1f}\n"
            f"{'Risk Level':<{R}}{uv_desc}\n\n"
            f"[bold #89b4fa]── Current Readings ────────────────────[/bold #89b4fa]\n"
            f"{'Pressure':<{R}}{float(c.get('surface_pressure',0)):.0f} hPa\n"
            f"{'Humidity':<{R}}{c.get('relative_humidity_2m','N/A')}%\n"
            f"{'Cloud Cover':<{R}}{c.get('cloud_cover','N/A')}%\n"
            f"{'Visibility':<{R}}"
            + (f"{float(c.get('visibility',0))/1000:.1f} km\n" if c.get('visibility') else "N/A\n")
            + f"\n[bold #89b4fa]── Data Source ─────────────────────────[/bold #89b4fa]\n"
            f"{'Provider':<{R}}Open-Meteo (open-meteo.com)\n"
            f"{'License':<{R}}CC BY 4.0\n"
        )

    # ── Events ─────────────────────────────────────────────────────────────────
    def on_list_view_highlighted(self, e: ListView.Highlighted):
        if e.item is None: return
        idx = self.query_one("#loc-list", ListView).index
        if idx is None or idx >= len(self._locations): return
        self._cur_idx = idx
        name = self._cur_loc["name"]
        if self._weather.get(name):
            self._draw_all()
        else:
            self._status(f"Loading {name}…")
            threading.Thread(target=self._fetch_loc, args=(self._cur_loc,), daemon=True).start()

    def on_tabbed_content_tab_activated(self, e: TabbedContent.TabActivated):
        try:    pid = e.pane.id
        except: pid = str(e.tab.id)
        data = self._weather.get(self._cur_loc["name"])
        if not data: return
        if pid == "tab-hourly":    self._draw_hourly(data)
        elif pid == "tab-weekly":  self._draw_weekly(data)
        elif pid == "tab-details": self._draw_details(data)

    def on_resize(self, _):
        data = self._weather.get(self._cur_loc["name"])
        if data: self._draw_hourly(data)

    # ── Actions ────────────────────────────────────────────────────────────────
    def action_add_location(self):
        def done(loc):
            if not loc: return
            if any(l["name"] == loc["name"] for l in self._locations):
                self._status(f"{loc['name']} already in list"); return
            self._locations.append(loc)
            _save(LOC_FILE, self._locations)
            lv = self.query_one("#loc-list", ListView)
            lv.append(ListItem(Label(loc["name"].split(",")[0][:18])))
            # Navigate to the new location immediately
            new_idx = len(self._locations) - 1
            self._cur_idx = new_idx
            lv.index = new_idx
            self._status(f"Added {loc['name']}, fetching…")
            threading.Thread(target=self._fetch_loc, args=(loc,), daemon=True).start()
        self.push_screen(SearchModal(), done)

    def action_del_location(self):
        lv = self.query_one("#loc-list", ListView)
        idx = lv.index
        if idx is None or len(self._locations) <= 1: return
        self._locations.pop(idx); _save(LOC_FILE, self._locations); lv.pop(idx)
        self._cur_idx = min(idx, len(self._locations)-1)
        self._draw_all()

    def action_refresh(self):
        self._status("Refreshing…")
        threading.Thread(target=self._boot, daemon=True).start()

    def action_toggle_unit(self):
        self._unit = "fahrenheit" if self._unit == "celsius" else "celsius"
        self._weather.clear()
        self._rebuild_list()
        self._status("Switching units, refetching…")
        threading.Thread(target=self._boot, daemon=True).start()

    def action_cursor_down(self): self.query_one("#loc-list", ListView).action_cursor_down()
    def action_cursor_up(self):   self.query_one("#loc-list", ListView).action_cursor_up()

    def _status(self, msg: str):
        try: self.query_one("#status").update(f"[#585b70]{msg}[/#585b70]")
        except Exception: pass


def run():
    WeatherApp().run()

if __name__ == "__main__":
    run()
