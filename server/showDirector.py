"""
Runs the haunted house show for one group at a time, from where people are.

It only decides what should happen and when. Call update() a few times a second
with the time and the zone each person inside is in; it returns cues for the
caller to act on (play an announcement, start the Bee Gees, trigger the scare).
The radar map app shows the cues as messages for dry runs, and the haunted
house loop can act on the same cues for real.

The run:
    empty      nothing happens until someone gets past the stairs
    welcome    someone reached Backstage or Main stage: "the show begins shortly"
    seating    25 s after anyone reached Backstage, or 5 s after anyone reached
               Main stage, whichever comes first: "take your seats, the show is
               about to begin". 15 s later, start, assuming they're seated (the
               radar often loses seated people, so it doesn't check)
    show       Bee Gees for about 15 s, then the scare
    scare      the startle plays
    leaving    2.5 s after the startle: thank them and point them to the exit.
               Reset once everyone is out (or 2 minutes after the scare, in case
               of phantoms)
"""

import random
from dataclasses import dataclass

BACKSTAGE = "Backstage"        # zone labels in radarMaps/layout.json
MAIN_STAGE = "Main stage"
ENTRANCE = "Entrance"          # in the entrance but past the stairs: at the door or outside, so not inside
BACKSTAGE_SECONDS = 25         # first person in Backstage -> "take your seats"...
MAIN_STAGE_SECONDS = 5         # ...or first person in Main stage -> "take your seats", whichever is first
SEATING_SECONDS = 15           # "take your seats" -> show
SHOW_SECONDS = 15              # Bee Gees before the scare...
SHOW_JITTER_SECONDS = 2.5      # ...give or take this much
SCARE_SECONDS = 15.4           # the startle clip, from its 2.6 s start point to the end
OUTRO_DELAY_SECONDS = 2.5      # startle finished -> final announcement (its end is already a wind-down)
RESET_AFTER_SCARE_SECONDS = 120  # reset this long after the scare even if phantoms still look like people
TAKE_SEATS = "Please take your seats. The show is about to begin."


@dataclass
class Cue:
    name: str   # welcome, take_seats, show_start, scare, outro, reset, reconnect_radar
    text: str   # what's happening, for messages and logs


class ShowDirector:
    def __init__(self):
        self.state = "empty"
        self._since = 0.0          # when the current state (or seating announcement) started
        self._first_in = {}        # zone -> when someone first reached it, during the welcome
        self._scare_at = 0.0
        self._manual = False       # started by hand: ignore the radar until the next reset

    def update(self, now, zones):
        """now: seconds (any steady clock). zones: one entry per tracked person, the zone label
        they're in, ENTRANCE if they're at the door past the stairs, or None.
        Returns a list of Cues to act on now."""
        zones = [zone for zone in zones if zone != ENTRANCE]  # at the door counts as gone
        inside = len(zones) > 0 or self._manual
        main_stage = zones.count(MAIN_STAGE)
        elapsed = now - self._since

        if self.state == "empty":
            if BACKSTAGE in zones or MAIN_STAGE in zones:
                self._first_in = {zone: now for zone in (BACKSTAGE, MAIN_STAGE) if zone in zones}
                return self._go("welcome", now, Cue("welcome", "Welcome! The show begins shortly."))

        elif self.state in ("welcome", "seating") and not inside:
            return self._go("empty", now, Cue("reset", "Everyone left before the show. Resetting."))

        elif self.state == "welcome":
            for zone in (BACKSTAGE, MAIN_STAGE):
                if zone in zones:
                    self._first_in.setdefault(zone, now)
            if self._seats_due(now) <= 0:
                return self._go("seating", now, Cue("take_seats", TAKE_SEATS))

        elif self.state == "seating" and elapsed >= SEATING_SECONDS:
            self._scare_at = now + SHOW_SECONDS + random.uniform(-SHOW_JITTER_SECONDS, SHOW_JITTER_SECONDS)
            return self._go("show", now, Cue("show_start", f"Show starting: {main_stage} seen in Main stage. "
                                                           f"Scare in {self._scare_at - now:.0f} s."))

        elif self.state == "show" and now >= self._scare_at:
            return self._go("scare", now, Cue("scare", "SCARE!"))

        elif self.state == "scare" and elapsed >= SCARE_SECONDS + OUTRO_DELAY_SECONDS:
            return self._go("leaving", now, Cue("outro", "Bee Gees down. Thank you for coming! "
                                                         "Please leave through the marked exit."))

        elif self.state == "leaving":
            if not inside:
                return self._go("empty", now, Cue("reset", "Everyone is out. Ready for the next group."))
            if now - self._scare_at >= RESET_AFTER_SCARE_SECONDS:
                if self._manual:
                    return self._go("empty", now, Cue("reset", "Show started by hand is over. Ready for the next group."))
                return self._go("empty", now,
                                Cue("reset", f"Still {len(zones)} inside {RESET_AFTER_SCARE_SECONDS // 60} minutes "
                                             f"after the scare, probably phantoms. Resetting."),
                                Cue("reconnect_radar", "Clearing people and reconnecting the radar."))
        return []

    def status(self, now):
        """One line describing where the show is, for display."""
        elapsed = now - self._since
        if self.state == "empty":
            return "Waiting for a group"
        if self.state == "welcome":
            due = self._seats_due(now)
            return f"Welcome: take-your-seats call in {due:.0f} s" if due < float("inf") else "Welcome"
        if self.state == "seating":
            return f"Seating: show starts in {SEATING_SECONDS - elapsed:.0f} s"
        if self.state == "show":
            return f"Show: scare in {self._scare_at - now:.0f} s"
        if self.state == "scare":
            return f"Scare: final announcement in {SCARE_SECONDS + OUTRO_DELAY_SECONDS - elapsed:.0f} s"
        reset_in = RESET_AFTER_SCARE_SECONDS - (now - self._scare_at)
        return f"Leaving: waiting for everyone to exit (reset in {reset_in:.0f} s regardless)"

    def _seats_due(self, now):
        """Seconds until the take-your-seats call, during the welcome (inf if nobody has reached a stage)."""
        waits = {BACKSTAGE: BACKSTAGE_SECONDS, MAIN_STAGE: MAIN_STAGE_SECONDS}
        return min((since + waits[zone] - now for zone, since in self._first_in.items()), default=float("inf"))

    def start(self, now):
        """Run the show from the welcome on, whatever the radar sees (for when it has lost a group that's
        inside). Until the next reset the group counts as inside; it resets by itself
        RESET_AFTER_SCARE_SECONDS after the scare."""
        self._manual = True
        self._first_in = {BACKSTAGE: now}
        return self._go("welcome", now, Cue("welcome", "Started by hand. Welcome! The show begins shortly."))

    def reset(self, now):
        """Back to waiting for a group, e.g. when the operator resets by hand."""
        return self._go("empty", now, Cue("reset", "Manual reset. Ready for the next group."))

    def _go(self, state, now, *cues):
        if state == "empty":
            self._manual = False
        self.state = state
        self._since = now
        return list(cues)
