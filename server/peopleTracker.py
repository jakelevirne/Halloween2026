"""
Turns the LD2460's per-report target lists into people with stable IDs.

The radar sends an unordered list of positions with no IDs, and it reports
reflections (like the garage door) as targets too. The house has one way in
and out, at the top of the stairs, so:
  - a new track only counts as a person once it has been in the entrance.
    Reflections show up in the middle of the room and never get there.
  - a person who drops out anywhere but the entrance is probably still inside,
    so they're kept at their last position for a while ("lost").
"""

import itertools
import math
from dataclasses import dataclass

MATCH_DISTANCE_M = 0.6   # a detection this close to a track continues it
RECOVER_DISTANCE_M = 1.5 # a detection no track claimed can continue a missing person this far away
WALK_SPEED_MPS = 1.5     # how much further an unseen person could have moved, per second
MAX_MATCH_M = 3.0
DROP_SECONDS = 1.0       # unconfirmed tracks, and people last seen in the entrance (they left), go after this
LOST_SECONDS = 10.0      # people who drop out inside the house are kept this long


@dataclass
class Track:
    id: int
    x: float
    y: float
    last_seen: float
    confirmed: bool = False  # has been in the entrance, so it's a real person

    def lost(self, now):
        return now - self.last_seen > 0.5


class PeopleTracker:
    def __init__(self, entrance=None):
        """entrance: {"x0", "y0", "x1", "y1"} in meters. Without one, every track counts as a person."""
        self.entrance = entrance
        self.tracks = []
        self._ids = itertools.count(1)

    def in_entrance(self, x, y):
        e = self.entrance
        return e is None or (e["x0"] <= x <= e["x1"] and e["y0"] <= y <= e["y1"])

    def update(self, detections, now):
        """detections: [(x, y), ...] from one radar report.
        Returns (people, others): confirmed tracks and unconfirmed ones (likely reflections)."""
        matched_tracks, matched_detections = set(), set()

        def match(tracks, base_reach):
            # Continue tracks with the nearest detections, closest pairs first
            pairs = []
            for track in tracks:
                reach = min(MAX_MATCH_M, base_reach + WALK_SPEED_MPS * (now - track.last_seen))
                for i, (x, y) in enumerate(detections):
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
        # arrival (people only arrive at the entrance), so let missing people pick it up from further away
        match([t for t in self.tracks if t.confirmed and t.id not in matched_tracks], RECOVER_DISTANCE_M)

        # Anything left over starts a new track
        for i, (x, y) in enumerate(detections):
            if i not in matched_detections:
                self.tracks.append(Track(next(self._ids), x, y, now))

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
