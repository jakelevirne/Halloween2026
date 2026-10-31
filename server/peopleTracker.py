"""
Turns the LD2460's per-report target lists into people with stable IDs.

The radar sends an unordered list of positions with no IDs, and it reports
reflections (like the garage door) as targets too. The house has one way in
and out, at the top of the stairs, so:
  - a new track only counts as a person once it has been in the entrance.
    Reflections show up in the middle of the room and never get there.
  - a person who drops out anywhere but the entrance is still inside, so
    they're kept at their last position ("lost") until they move again. The
    radar stops reporting people who sit still, like guests during the show.
  - reflections don't walk. When someone walks under one, the radar can merge
    them, then either send the reflection's target off with the person or
    leave the person's track parked on the reflection. So an unconfirmed track
    that walks is a person: it's handed to a person who got lost where it
    started, or to a person parked on a known reflection spot.
"""

import itertools
import math
from dataclasses import dataclass

MATCH_DISTANCE_M = 0.6   # a detection this close to a track continues it
RECOVER_DISTANCE_M = 1.5 # a detection no track claimed can continue a missing person this far away
WALK_SPEED_MPS = 1.5     # how much further an unseen person could have moved, per second
MAX_MATCH_M = 3.0
WALK_AWAY_M = 0.8        # an unconfirmed track that moves this far from where it started is walking
HANDOFF_M = 1.0          # ...and is taken over by a person lost within this distance of its start
DROP_SECONDS = 1.0       # unconfirmed tracks, and people last seen in the entrance (they left), go after this
LOST_SECONDS = 300.0     # people who drop out inside the house are kept this long (longer than the show)


@dataclass
class Track:
    id: int
    x: float
    y: float
    last_seen: float
    confirmed: bool = False  # has been in the entrance, so it's a real person

    def __post_init__(self):
        self.start = (self.x, self.y)

    def lost(self, now):
        return now - self.last_seen > 0.5


class PeopleTracker:
    def __init__(self, entrance=None, reflections=()):
        """entrance: {"x0", "y0", "x1", "y1"} in meters. Without one, every track counts as a person.
        reflections: spots where the radar sees things that aren't people, each a circle
        {"x", "y", "r"} or a rectangle {"x0", "y0", "x1", "y1"}, in meters."""
        self.entrance = entrance
        self.reflections = list(reflections)
        self.tracks = []
        self._ids = itertools.count(1)

    def in_entrance(self, x, y):
        e = self.entrance
        return e is None or (e["x0"] <= x <= e["x1"] and e["y0"] <= y <= e["y1"])

    def on_reflection(self, x, y):
        for spot in self.reflections:
            if "r" in spot:
                if math.dist((x, y), (spot["x"], spot["y"])) <= spot["r"]:
                    return True
            elif spot["x0"] <= x <= spot["x1"] and spot["y0"] <= y <= spot["y1"]:
                return True
        return False

    def update(self, detections, now):
        """detections: [(x, y), ...] from one radar report.
        Returns (people, others): confirmed tracks and unconfirmed ones (likely reflections)."""
        matched_tracks, matched_detections = set(), set()

        def match(tracks, base_reach, skip_entrance=False):
            # Continue tracks with the nearest detections, closest pairs first
            pairs = []
            for track in tracks:
                reach = min(MAX_MATCH_M, base_reach + WALK_SPEED_MPS * (now - track.last_seen))
                for i, (x, y) in enumerate(detections):
                    if skip_entrance and self.in_entrance(x, y):
                        continue
                    distance = math.dist((track.x, track.y), (x, y))
                    if distance <= reach and i not in matched_detections:
                        pairs.append((distance, track.id, i, track))
            for _, track_id, i, track in sorted(pairs, key=lambda p: p[:3]):
                if track_id in matched_tracks or i in matched_detections:
                    continue
                matched_tracks.add(track_id)
                matched_detections.add(i)
                track.x, track.y = detections[i]
                track.last_seen = now

        match(self.tracks, MATCH_DISTANCE_M)
        # A detection nothing claimed is more likely a person who jumped or dropped out than a new
        # arrival (people only arrive at the entrance), so let missing people pick it up from further away.
        # Not in the entrance, though: that's a new guest.
        match([t for t in self.tracks if t.confirmed and t.id not in matched_tracks], RECOVER_DISTANCE_M,
              skip_entrance=True)

        # Anything left over starts a new track
        for i, (x, y) in enumerate(detections):
            if i not in matched_detections:
                self.tracks.append(Track(next(self._ids), x, y, now))

        # Unconfirmed tracks that walk are people the radar mixed up with a reflection
        for track in [t for t in self.tracks if not t.confirmed and t.last_seen == now]:
            if math.dist(track.start, (track.x, track.y)) < WALK_AWAY_M or self.on_reflection(track.x, track.y):
                continue
            # A person who got lost where this track started...
            lost = [p for p in self.tracks
                    if p.confirmed and p.lost(now) and math.dist((p.x, p.y), track.start) <= HANDOFF_M]
            # ...or a person parked on a reflection spot, whose track is really following the reflection
            parked = [p for p in self.tracks if p.confirmed and not p.lost(now) and self.on_reflection(p.x, p.y)]
            if lost:
                person = min(lost, key=lambda p: math.dist((p.x, p.y), track.start))
            elif parked:
                person = min(parked, key=lambda p: math.dist((p.x, p.y), (track.x, track.y)))
                self.tracks.append(Track(next(self._ids), person.x, person.y, person.last_seen))  # the reflection
            else:
                continue
            person.x, person.y, person.last_seen = track.x, track.y, now
            self.tracks.remove(track)

        for track in self.tracks:
            if track.last_seen == now and self.in_entrance(track.x, track.y):
                track.confirmed = True

        def keep(track):
            unseen = now - track.last_seen
            if not track.confirmed or self.in_entrance(track.x, track.y):
                return unseen <= DROP_SECONDS
            return unseen <= LOST_SECONDS

        self.tracks = [t for t in self.tracks if keep(t)]
        return [t for t in self.tracks if t.confirmed], [t for t in self.tracks if not t.confirmed]
