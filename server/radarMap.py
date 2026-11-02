#!/usr/bin/env python3
"""
Live map of people tracked by the LD2460 radar (test app).

Draws the radar's view from above, like HLKRadarTool's map: range rings,
the ceiling-mode detection area, and a dot with a short trail for each person.
The dashed outline is the room, assuming the radar is in the middle of it.

Mapping mode: press m, walk the perimeter (and anything else worth mapping),
press m again. The walk is saved to radarMaps/walk-<date>-<time>.csv and drawn
as breadcrumbs. Press w to review saved walks without the live view (w again
for older ones, Esc to go back to live).

Layout: in review mode, drag to draw a rectangle (a room, a prop, a trigger
zone), type a label and press Enter. Right-click (two-finger click) a rectangle
to delete it. Rectangles are saved to radarMaps/layout.json and always drawn.

People: targets are tracked with peopleTracker.py and only count as people
once they've come through the entrance (the dashed yellow box in layout.json).
Anything else, like the garage door reflection, shows as a grey x. Someone
already in the room when the app starts needs to walk through the entrance.

Show: showDirector.py runs the show from where people are. Its cues (welcome,
take your seats, show, scare, goodbye, reset) appear as messages in the panel at
the top, and showPlayer.py plays them: the still image / Bee Gees / startle in a
Show window, show audio on output 6 and announcements on output 5 of the UMC1820.
With --dry-run nothing is played, for checking the logic.

Every radar report is also written to radarMaps/live.log (cleared on startup),
along with the show cues (lines starting SHOW), so the live data can be
followed with: tail -f radarMaps/live.log

Web view: the map is also served at http://<this mac>:8460 for a phone or another
computer on the same network (or Tailscale). The page shows a picture of the map,
refreshed about twice a second, with Start show and Reset show buttons.

Manual reset: press r in the map window (or Reset show on the web view) to clear
everyone and put the show back to waiting for a group.
Manual start: Start show on the web view runs the show from the welcome on,
ignoring the radar, for when it has lost a group that's inside.

Usage:
    uv run radarMap.py
    uv run radarMap.py --dry-run     # show cues as messages only, no video or sound

Close HLKRadarTool on your phone first: the radar takes one connection at a time.
"""

import argparse
import asyncio
import csv
import io
import json
import shutil
import socket
import subprocess
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from matplotlib.colors import to_rgba
from matplotlib.patches import Circle, Rectangle
from matplotlib.widgets import TextBox
from PIL import Image

from ld2460Radar import watch
from peopleTracker import PeopleTracker
from showDirector import ENTRANCE, ShowDirector

MAX_RANGE_M = 5
DETECTION_RADIUS_M = 4  # ceiling mode covers about 4 m around the radar
ROOM_WIDTH_FT = 15      # along x. Swap these if the room turns out to be rotated on the map.
ROOM_DEPTH_FT = 20      # along y
FEET_PER_M = 3.2808
TRAIL_SECONDS = 2
BACKGROUND = "#2f6fcf"
COLORS = ["#ffd400", "#00e5ff", "#ff5cf4", "#7dff6b", "#ff8c42"]  # picked by person ID
WALKS_DIR = Path(__file__).resolve().parent / "radarMaps"
LAYOUT_FILE = WALKS_DIR / "layout.json"
LIVE_LOG = WALKS_DIR / "live.log"  # every report, one line each, cleared on startup (tail -f it)
AREA_COLOR = "#7dff6b"
WEB_PORT = 8460
WEB_FRAME_SECONDS = 0.5  # how often the web view's picture is refreshed
WEB_WIDTH = 900          # pixels
WEB_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Radar map</title>
<style>
  body { margin: 0; background: #2f6fcf; font-family: -apple-system, sans-serif; }
  img { display: block; width: 100%; max-width: 900px; margin: auto; }
  .controls { text-align: center; padding: 12px; color: white; }
  button { font-size: 18px; padding: 10px 24px; border: 0; border-radius: 8px; background: #ff4040; color: white; }
  #start { background: #20a040; margin-right: 12px; }
</style>
</head><body><img id="map" src="map.jpg" alt="radar map">
<div class="controls"><button id="start">Start show</button><button id="reset">Reset show</button>
<span id="note"></span></div>
<script>
  // Start: runs the show from the welcome on, ignoring the radar.
  // Reset: clears everyone and puts the show back to waiting for a group.
  async function send(action, question) {
    if (!confirm(question)) return;
    const note = document.getElementById("note");
    try {
      const response = await fetch(action, { method: "POST" });
      note.textContent = response.ok ? `${action} sent` : `${action} failed`;
    } catch (e) {
      note.textContent = `${action} failed`;
    }
    setTimeout(() => note.textContent = "", 3000);
  }
  document.getElementById("start").onclick = () => send("start", "Start the show now, ignoring the radar?");
  document.getElementById("reset").onclick = () => send("reset", "Reset the show and clear everyone from the map?");

  // Load each new frame off screen and swap it in, so the picture never flickers
  const map = document.getElementById("map");
  function next() {
    const frame = new Image();
    frame.onload = () => { map.src = frame.src; setTimeout(next, 400); };
    frame.onerror = () => setTimeout(next, 2000);
    frame.src = "map.jpg?" + Date.now();
  }
  next();
</script></body></html>
"""


class RadarState:
    """Latest reports from the radar thread, read by the drawing code."""

    def __init__(self):
        self.lock = threading.Lock()
        # (time, people, others), each a list of (id, x, y, lost). Others are unconfirmed tracks.
        self.reports = deque(maxlen=200)
        self.mapping = False
        self.walk = []  # (seconds since mapping started, target, x, y)
        self._walk_start = 0
        self.log_file = None
        self.tracker = PeopleTracker(load_entrance(), load_reflections())
        self.director = ShowDirector()
        self.request_reconnect = lambda: None  # set by start_radar_thread
        self.reset_requested = threading.Event()  # r key or the web view's Reset button; handled on the GUI thread
        self.start_requested = threading.Event()  # the web view's Start button
        self.on_cue = lambda name: None  # set to the show player's send in main()

    def people_now(self):
        """Everyone the tracker counts as inside, lost or not: [(x, y), ...]."""
        with self.lock:
            return [(t.x, t.y) for t in self.tracker.tracks if t.confirmed]

    def reset_tracker(self):
        with self.lock:
            self.tracker = PeopleTracker(self.tracker.entrance, self.tracker.reflections)

    def log(self, text):
        if self.log_file:
            with self.lock:
                self.log_file.write(time.strftime("%H:%M:%S") + f"  {text}\n")

    def open_live_log(self):
        WALKS_DIR.mkdir(exist_ok=True)
        self.log_file = open(LIVE_LOG, "w", buffering=1)  # line buffered, so tail -f sees each report

    def add(self, targets):
        with self.lock:
            now = time.monotonic()
            people, others = self.tracker.update(targets, now)
            people = [(t.id, t.x, t.y, t.lost(now)) for t in people]
            others = [(t.id, t.x, t.y, t.lost(now)) for t in others]
            self.reports.append((now, people, others))
            if self.mapping:
                self.walk += [(now - self._walk_start, i, x, y) for i, (x, y) in enumerate(targets)]
            if self.log_file:
                timestamp = time.strftime("%H:%M:%S") + f".{int(time.time() * 1000) % 1000:03d}"
                tracked = "  ".join(f"#{i} x={x:+.1f} y={y:+.1f}{' lost' if lost else ''}" for i, x, y, lost in people)
                unconfirmed = "  ".join(f"?{i} x={x:+.1f} y={y:+.1f}" for i, x, y, lost in others if not lost)
                self.log_file.write(f"{timestamp}  people={len(people)}  {tracked}  {unconfirmed}".rstrip() + "\n")

    def recent(self, seconds):
        cutoff = time.monotonic() - seconds
        with self.lock:
            return [r for r in self.reports if r[0] >= cutoff]

    def walk_points(self):
        with self.lock:
            return list(self.walk)

    def toggle_mapping(self):
        """Start or stop mapping. Returns the saved file when stopping, else None."""
        with self.lock:
            self.mapping = not self.mapping
            if self.mapping:
                self.walk = []
                self._walk_start = time.monotonic()
                return None
            walk = list(self.walk)
        return save_walk(walk) if walk else None


def save_walk(walk):
    WALKS_DIR.mkdir(exist_ok=True)
    path = WALKS_DIR / time.strftime("walk-%Y%m%d-%H%M%S.csv")
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["seconds", "target", "x_m", "y_m"])
        writer.writerows((f"{t:.2f}", i, f"{x:.2f}", f"{y:.2f}") for t, i, x, y in walk)
    return path


def list_walks():
    """Saved walks, newest first."""
    return sorted(WALKS_DIR.glob("walk-*.csv"), reverse=True)


def load_walk(path):
    with open(path) as f:
        return [(float(r["seconds"]), int(r["target"]), float(r["x_m"]), float(r["y_m"])) for r in csv.DictReader(f)]


def load_layout():
    """Labeled rectangles: [{"label", "x0", "y0", "x1", "y1"}, ...] in meters, x0 < x1 and y0 < y1."""
    if not LAYOUT_FILE.exists():
        return []
    return json.loads(LAYOUT_FILE.read_text())["areas"]


def load_reflections():
    """Spots where the radar sees things that aren't people (like the disco ball):
    [{"label", "x", "y", "r"}, ...] in meters. Nothing is dropped there; the tracker uses
    them to tell a person apart from a reflection they walked under."""
    if not LAYOUT_FILE.exists():
        return []
    return json.loads(LAYOUT_FILE.read_text()).get("reflections", [])


def load_entrance():
    """The only way in and out: {"x0", "y0", "x1", "y1"} in meters, or None."""
    if not LAYOUT_FILE.exists():
        return None
    return json.loads(LAYOUT_FILE.read_text()).get("entrance")


def save_layout(areas):
    # Keep the rest of the file (entrance, reflections) and replace just the areas
    layout = json.loads(LAYOUT_FILE.read_text()) if LAYOUT_FILE.exists() else {}
    layout["areas"] = areas
    WALKS_DIR.mkdir(exist_ok=True)
    LAYOUT_FILE.write_text(json.dumps(layout, indent=2) + "\n")


def start_radar_thread(state):
    loop = asyncio.new_event_loop()
    reconnect = asyncio.Event()
    thread = threading.Thread(target=lambda: loop.run_until_complete(watch(state.add, reconnect=reconnect)),
                              daemon=True)
    thread.start()
    state.request_reconnect = lambda: loop.call_soon_threadsafe(reconnect.set)


def make_map(state):
    """Build the figure. Returns (figure, update function for FuncAnimation)."""
    fig, ax = plt.subplots(figsize=(8, 9.8))
    fig.subplots_adjust(left=0.02, right=0.98, top=0.83, bottom=0.04)  # room above the map for the show panel
    fig.patch.set_facecolor(BACKGROUND)
    ax.set_facecolor(BACKGROUND)
    ax.set_aspect("equal")
    limit = MAX_RANGE_M + 0.3
    ax.set_xlim(-limit, limit)
    ax.set_ylim(-limit, limit)
    ax.axis("off")

    ax.add_patch(Circle((0, 0), DETECTION_RADIUS_M, color="#a0305a", alpha=0.6, lw=0))
    for r in range(1, MAX_RANGE_M + 1):
        ax.add_patch(Circle((0, 0), r, fill=False, ec="white", lw=1, alpha=0.8))
        ax.text(0.08, r - 0.25, f"{r}m", color="white", fontsize=10)
    ax.annotate("", xy=(MAX_RANGE_M + 0.2, 0), xytext=(-MAX_RANGE_M, 0),
                arrowprops=dict(arrowstyle="->", color="white", lw=1))
    ax.annotate("", xy=(0, MAX_RANGE_M + 0.2), xytext=(0, -MAX_RANGE_M),
                arrowprops=dict(arrowstyle="->", color="white", lw=1))
    ax.text(MAX_RANGE_M + 0.1, 0.15, "x", color="white", fontsize=12)
    ax.text(0.15, MAX_RANGE_M + 0.05, "y", color="white", fontsize=12)

    room_w = ROOM_WIDTH_FT / FEET_PER_M
    room_d = ROOM_DEPTH_FT / FEET_PER_M
    ax.add_patch(Rectangle((-room_w / 2, -room_d / 2), room_w, room_d,
                           fill=False, ec="white", ls="--", lw=1.5, alpha=0.9))
    ax.text(-room_w / 2 + 0.1, -room_d / 2 + 0.1, f"room {ROOM_WIDTH_FT}×{ROOM_DEPTH_FT} ft",
            color="white", fontsize=9, alpha=0.9)

    # Mapping walk: line through target #1's path, dots for everything recorded
    walk_line, = ax.plot([], [], color="white", lw=1, alpha=0.6)
    walk_dots = ax.scatter([], [], s=8, c="white", lw=0, alpha=0.9)
    walks = list_walks()
    walk_index = 0
    reviewing = False  # w pressed: show only the saved walk, no live people
    saved_walk = load_walk(walks[0]) if walks else []
    help_text = ax.text(-limit, -limit, "", color="white", fontsize=10, va="bottom")

    # Layout rectangles, drawn by dragging in review mode
    areas = load_layout()
    area_artists = []
    area_patches = []  # one per area, in the same order, for highlighting

    def draw_areas():
        for artist in area_artists:
            artist.remove()
        area_artists.clear()
        area_patches.clear()
        for area in areas:
            x0, y0, x1, y1 = area["x0"], area["y0"], area["x1"], area["y1"]
            patch = ax.add_patch(Rectangle((x0, y0), x1 - x0, y1 - y0, fc=to_rgba(AREA_COLOR, 0.2),
                                           ec=AREA_COLOR, lw=2, zorder=2))
            area_patches.append(patch)
            area_artists.append(patch)
            area_artists.append(ax.text((x0 + x1) / 2, (y0 + y1) / 2, area["label"], color="white", fontsize=10,
                                        fontweight="bold", ha="center", va="center", zorder=2))

    # Reflection spots: where the radar sees things that aren't people
    for spot in state.tracker.reflections:
        style = dict(fill=False, ec="#ff8c42", ls="--", lw=2, zorder=3)
        if "r" in spot:
            ax.add_patch(Circle((spot["x"], spot["y"]), spot["r"], **style))
            label_x, label_y = spot["x"], spot["y"] + spot["r"] + 0.05
        else:
            ax.add_patch(Rectangle((spot["x0"], spot["y0"]), spot["x1"] - spot["x0"], spot["y1"] - spot["y0"], **style))
            label_x, label_y = (spot["x0"] + spot["x1"]) / 2, spot["y1"] + 0.05
        ax.text(label_x, label_y, f"{spot['label']} (reflection)", color="white", fontsize=9,
                ha="center", va="bottom", zorder=3,
                bbox=dict(boxstyle="round,pad=0.2", fc="#ff8c42", alpha=0.8, lw=0))

    entrance = state.tracker.entrance
    if entrance:
        ax.add_patch(Rectangle((entrance["x0"], entrance["y0"]), entrance["x1"] - entrance["x0"],
                               entrance["y1"] - entrance["y0"], fill=False, ec="#ffd400", ls="--", lw=2, zorder=3))
        ax.text(entrance["x1"] + 0.1, (entrance["y0"] + entrance["y1"]) / 2, "Entrance", color="#ffd400",
                fontsize=9, fontweight="bold", va="center", zorder=3)

    def area_at(x, y):
        """The smallest area containing (x, y), or None."""
        hits = [a for a in areas if a["x0"] <= x <= a["x1"] and a["y0"] <= y <= a["y1"]]
        return min(hits, key=lambda a: (a["x1"] - a["x0"]) * (a["y1"] - a["y0"]), default=None)

    draw_areas()
    preview = ax.add_patch(Rectangle((0, 0), 0, 0, fill=False, ec=AREA_COLOR, lw=2, ls="--", zorder=5, visible=False))
    drag_start = None
    pending = None  # drawn rectangle waiting for its label
    label_ax = fig.add_axes([0.3, 0.015, 0.45, 0.04])
    label_box = TextBox(label_ax, "Label ")
    label_box.label.set_color("white")
    label_ax.set_visible(False)

    trail = ax.scatter([], [], s=40, lw=0)
    unconfirmed = ax.scatter([], [], s=80, marker="x", c="#c0c0c0", linewidths=2, zorder=3)
    dots = ax.scatter([], [], s=260, edgecolors="black", linewidths=1.5, zorder=3)
    labels = [ax.text(0, 0, "", fontsize=10, fontweight="bold", zorder=4,
                      bbox=dict(boxstyle="round,pad=0.2", fc="black", alpha=0.6, lw=0))
              for _ in COLORS]
    status = ax.text(-limit, limit, "", color="white", fontsize=13, va="top")
    nan_point = [(float("nan"), float("nan"))]

    # Show dry run: the director's status and recent cues
    show_text = fig.text(0.02, 0.985, "", color="white", fontsize=9, family="monospace", va="top",
                         bbox=dict(boxstyle="round,pad=0.4", fc="black", alpha=0.5, lw=0))
    cue_log = deque(maxlen=6)

    def zone_for_show(x, y):
        # The Stairs zone overlaps the bottom of the entrance, so the entrance only wins past the stairs
        area = area_at(x, y)
        if area:
            return area["label"]
        return ENTRANCE if state.tracker.in_entrance(x, y) else None

    def run_show(now):
        cues = []
        if state.reset_requested.is_set():
            state.reset_requested.clear()
            state.reset_tracker()
            cues += state.director.reset(now)
        if state.start_requested.is_set():
            state.start_requested.clear()
            cues += state.director.start(now)
        zones = [zone_for_show(x, y) for x, y in state.people_now()]
        for cue in cues + state.director.update(now, zones):
            line = f"{time.strftime('%H:%M:%S')}  {cue.text}"
            cue_log.append(line)
            print(f"SHOW {line}")
            state.log(f"SHOW {cue.name}: {cue.text}")
            state.on_cue(cue.name)
            if cue.name == "reconnect_radar":
                state.reset_tracker()
                state.request_reconnect()
        count = sum(zone != ENTRANCE for zone in zones)
        inside = f"{count} inside" + (f" ({', '.join(sorted(z or 'no zone' for z in zones))})" if zones else "")
        show_text.set_text("\n".join([f"SHOW  {state.director.status(now)}", f"      {inside}", *cue_log]))

    def update(_frame=None):
        now = time.monotonic()
        run_show(now)
        reports = state.recent(TRAIL_SECONDS)

        # Trail: earlier positions of people, fading out with age
        points, colors = [], []
        for t, people, _ in reports[:-1]:
            fade = 1 - (now - t) / TRAIL_SECONDS
            for person_id, x, y, lost in people:
                if not lost:
                    points.append((x, y))
                    colors.append(to_rgba(COLORS[person_id % len(COLORS)], alpha=0.6 * fade))
        trail.set_offsets(points or nan_point)
        trail.set_facecolors(colors or ["none"])

        # Current positions. Lost people (not seen for a moment) are drawn faded.
        recent = reports and now - reports[-1][0] < 1
        people = reports[-1][1][:len(labels)] if recent else []
        others = [(x, y) for _, x, y, lost in reports[-1][2] if not lost] if recent else []
        if reviewing:
            people, others = [], []
            trail.set_offsets(nan_point)
        dots.set_offsets([(x, y) for _, x, y, _ in people] or nan_point)
        dots.set_facecolors([to_rgba(COLORS[i % len(COLORS)], 0.3 if lost else 1) for i, _, _, lost in people]
                            or ["none"])
        unconfirmed.set_offsets(others or nan_point)
        occupied = [area_at(x, y) for _, x, y, _ in people]
        for i, label in enumerate(labels):
            if i < len(people):
                person_id, x, y, lost = people[i]
                label.set_position((x + 0.2, y + 0.2))
                where = f"  in {occupied[i]['label']}" if occupied[i] else ""
                label.set_text(f"#{person_id}  x={x:+.1f}m  y={y:+.1f}m{where}{'  (lost)' if lost else ''}")
                label.set_color(COLORS[person_id % len(COLORS)])
            else:
                label.set_text("")
        # Light up areas with someone in them
        for area, patch in zip(areas, area_patches):
            lit = any(area is o for o in occupied)
            patch.set_facecolor(to_rgba(AREA_COLOR, 0.6 if lit else 0.2))
            patch.set_linewidth(4 if lit else 2)

        # Walks only show while mapping or reviewing; live mode is just the layout and people
        walk = state.walk_points() if state.mapping else saved_walk if reviewing else []
        walk_line.set_data([x for _, i, x, y in walk if i == 0], [y for _, i, x, y in walk if i == 0])
        walk_dots.set_offsets([(x, y) for _, i, x, y in walk] or [(float("nan"), float("nan"))])

        if not reports:
            status.set_text("Waiting for radar…")
        else:
            rate = len(reports) / TRAIL_SECONDS
            count = "nobody" if not people else f"{len(people)} {'person' if len(people) == 1 else 'people'}"
            extra = f"  + {len(others)} unconfirmed" if others else ""
            status.set_text(f"{count}{extra}   ({rate:.0f} reports/s)")
        if state.mapping:
            status.set_text(f"● MAPPING  {len(walk)} points")
            status.set_color("#ff4040")
            help_text.set_text("Walk the walls. Press m to stop and save.")
        elif pending:
            status.set_text("Type a label and press Enter (empty label cancels)")
            status.set_color(AREA_COLOR)
            help_text.set_text("")
        elif reviewing:
            status.set_text(f"Reviewing walk {walk_index + 1} of {len(walks)} (live view hidden)")
            status.set_color("white")
            help_text.set_text(f"{walks[walk_index].name}   drag: draw rectangle   right-click: delete\n"
                               f"w: next walk   Esc: back to live")
        else:
            status.set_color("white")
            help_text.set_text("m: start mapping   w: review walks and draw the layout   r: reset the show")
        return [walk_line, walk_dots, trail, unconfirmed, dots, status, help_text, show_text, *labels]

    def on_key(event):
        nonlocal saved_walk, walks, walk_index, reviewing
        if label_box.capturekeystrokes or pending:
            return  # typing a label
        if event.key == "m":
            reviewing = False
            if state.mapping:
                saved_walk = state.walk_points()  # so w reviews the new walk first
            path = state.toggle_mapping()
            if path:
                walks, walk_index = list_walks(), 0
                print(f"Saved walk to {path}")
        elif event.key == "w" and walks and not state.mapping:
            # First press reviews the walk on screen, later presses step to older walks
            if reviewing:
                walk_index = (walk_index + 1) % len(walks)
                saved_walk = load_walk(walks[walk_index])
            reviewing = True
        elif event.key == "escape":
            reviewing = False
        elif event.key == "r":
            state.reset_requested.set()

    def snap(value):
        return round(value * 10) / 10  # the radar reports in 0.1 m steps

    def on_press(event):
        nonlocal drag_start
        if not reviewing or pending or event.inaxes is not ax:
            return
        if event.button == 1:
            drag_start = (snap(event.xdata), snap(event.ydata))
            preview.set_bounds(*drag_start, 0, 0)
        elif event.button == 3:
            # Delete the smallest rectangle under the pointer
            area = area_at(event.xdata, event.ydata)
            if area:
                areas.remove(area)
                save_layout(areas)
                draw_areas()

    def on_motion(event):
        if drag_start is None or event.inaxes is not ax:
            return
        x, y = snap(event.xdata), snap(event.ydata)
        preview.set_bounds(min(drag_start[0], x), min(drag_start[1], y), abs(x - drag_start[0]), abs(y - drag_start[1]))
        preview.set_visible(True)

    def on_release(event):
        nonlocal drag_start, pending
        if drag_start is None:
            return
        drag_start = None
        x0, y0, w, h = preview.get_x(), preview.get_y(), preview.get_width(), preview.get_height()
        if w < 0.2 or h < 0.2:  # just a click
            preview.set_visible(False)
            return
        label_box.set_val("")  # clear the last label first: set_val also fires a submit
        pending = {"x0": round(x0, 2), "y0": round(y0, 2), "x1": round(x0 + w, 2), "y1": round(y0 + h, 2)}
        label_ax.set_visible(True)
        label_box.begin_typing()

    def on_label(text):
        nonlocal pending
        if pending is None:
            return
        area, pending = pending, None
        if text.strip():
            areas.append({"label": text.strip(), **area})
            save_layout(areas)
            draw_areas()
        preview.set_visible(False)
        label_ax.set_visible(False)
        label_box.stop_typing()  # Enter submits but leaves the box capturing keys

    label_box.on_submit(on_label)
    fig.canvas.mpl_connect("key_press_event", on_key)
    fig.canvas.mpl_connect("button_press_event", on_press)
    fig.canvas.mpl_connect("motion_notify_event", on_motion)
    fig.canvas.mpl_connect("button_release_event", on_release)
    return fig, update


def start_web_view(fig, state):
    """Serve the map at http://<this mac>:WEB_PORT. A JPEG of the figure is grabbed after each
    redraw (at most every WEB_FRAME_SECONDS) on the GUI thread, since matplotlib isn't thread safe."""
    frame = {"jpeg": b"", "time": 0.0}

    def grab(_event):
        if time.monotonic() - frame["time"] < WEB_FRAME_SECONDS:
            return
        image = Image.frombuffer("RGBA", fig.canvas.get_width_height(physical=True), fig.canvas.buffer_rgba())
        image = image.convert("RGB")
        image = image.resize((WEB_WIDTH, round(image.height * WEB_WIDTH / image.width)))
        jpeg = io.BytesIO()
        image.save(jpeg, "JPEG", quality=75)
        frame["jpeg"], frame["time"] = jpeg.getvalue(), time.monotonic()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/":
                body, kind = WEB_PAGE.encode(), "text/html; charset=utf-8"
            elif self.path.startswith("/map.jpg"):
                body, kind = frame["jpeg"], "image/jpeg"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            requests = {"/reset": state.reset_requested, "/start": state.start_requested}
            if self.path not in requests:
                self.send_error(404)
                return
            requests[self.path].set()
            self.send_response(204)
            self.end_headers()

        def log_message(self, *args):
            pass  # no per-request noise in the terminal

    fig.canvas.mpl_connect("draw_event", grab)
    server = ThreadingHTTPServer(("0.0.0.0", WEB_PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    for address in web_addresses():
        print(f"Web view: http://{address}:{WEB_PORT}")
    return server


def web_addresses():
    """Ways other devices can reach this Mac: Bonjour name, home network IP, Tailscale name and IP."""
    def run(*command):
        try:
            return subprocess.run(command, capture_output=True, text=True, timeout=3).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            return ""

    addresses = []
    if bonjour := run("scutil", "--get", "LocalHostName"):
        addresses.append(f"{bonjour}.local")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("192.0.2.1", 9))  # no packet is sent; this just picks the outgoing interface
            addresses.append(s.getsockname()[0])
        except OSError:
            pass
    tailscale = shutil.which("tailscale") or "/Applications/Tailscale.app/Contents/MacOS/Tailscale"
    if status := run(tailscale, "status", "--self", "--peers=false"):
        ip, name = status.split()[:2]
        addresses += [name, ip]
    return addresses


def main():
    parser = argparse.ArgumentParser(description="Live radar map with the show running from it.")
    parser.add_argument("--dry-run", action="store_true", help="Show cues as messages only, no video or sound")
    args = parser.parse_args()

    state = RadarState()
    state.open_live_log()
    print(f"Logging every radar report to {LIVE_LOG}")
    start_radar_thread(state)
    fig, update = make_map(state)
    fig.canvas.manager.set_window_title("LD2460 radar map")
    start_web_view(fig, state)
    stop_player = None
    if not args.dry_run:
        import showPlayer  # only needed when playing media
        state.on_cue, stop_player = showPlayer.start_in_thread()
    _animation = FuncAnimation(fig, update, interval=50, cache_frame_data=False)  # keep a reference
    plt.show()
    if stop_player:
        stop_player()  # close the Show window too


if __name__ == "__main__":
    main()
