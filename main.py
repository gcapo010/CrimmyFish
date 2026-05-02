"""
main.py — Entry point and fishing state machine.

Fishing loop states:
  IDLE → CASTING → WAITING_BITE → HOOKING → FIGHTING → REELING → POST_CATCH → IDLE

Detection strategy (in priority order):
  1. Memory reader (memory_reader.py) — reads the game's own fishing-state integer
     directly from process memory.  Zero latency, 100 % reliable.  Requires a
     one-time scan with memory_scanner.py to find the address.
  2. CV fallback — vision.py frame-diff / template / phaseCorrelate detectors.
     Used automatically when memory.enabled=false or the address is unavailable.
"""

import threading
import time
from enum import Enum, auto
from typing import Optional

import calibration
import config
import gui as gui_module
import input_controller as ic
import logger
import memory_reader
import vision


class State(Enum):
    IDLE = auto()
    CASTING = auto()
    WAITING_BITE = auto()
    HOOKING = auto()
    FIGHTING = auto()
    REELING = auto()
    POST_CATCH = auto()
    STOPPED = auto()


class FishingLoop:
    def __init__(self, gui: "gui_module.FishingGUI"):
        self._gui = gui
        self._loop_count = 0
        self._thread: Optional[threading.Thread] = None
        self._cfg: dict = {}

    # ------------------------------------------------------------------
    # Thread lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        ic.set_stopped(False)
        ic.set_paused(False)
        self._cfg = config.load()
        memory_reader.get_reader(self._cfg)   # init singleton (no-op if disabled)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("Fishing loop started.")

    def stop(self) -> None:
        ic.set_stopped(True)
        logger.info("Fishing loop stopped.")

    def toggle_pause(self) -> None:
        new_paused = not ic.is_paused()
        ic.set_paused(new_paused)
        self._gui.set_paused(new_paused)
        logger.info("Loop %s.", "paused" if new_paused else "resumed")

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def _run(self) -> None:
        cfg = self._cfg
        delays = cfg["delays"]

        while not ic.is_stopped():
            self._status("Idle")
            ic.random_sleep(delays["loop_restart_min"], delays["loop_restart_max"])
            if ic.is_stopped():
                break

            self._cast(cfg, delays)
            if ic.is_stopped():
                break

            bit = self._wait_for_bite(cfg, delays)
            if ic.is_stopped():
                break
            if not bit:
                logger.info("Bite timeout — recasting.")
                continue

            self._hook(cfg, delays)
            if ic.is_stopped():
                break

            fight_ok = self._fight(cfg, delays)
            if ic.is_stopped():
                break
            if not fight_ok:
                logger.warning("Fight timed out — recasting.")
                continue

            self._reel(cfg, delays)
            if ic.is_stopped():
                break

            self._post_catch(cfg, delays)
            if ic.is_stopped():
                break

            self._loop_count += 1
            self._gui.set_loop_count(self._loop_count)
            logger.info("Fish stowed. Total: %d", self._loop_count)

        self._status("Stopped")

    # ------------------------------------------------------------------
    # Phase: Cast
    # ------------------------------------------------------------------

    def _cast(self, cfg: dict, delays: dict) -> None:
        self._status("Casting…")
        cast_cfg = cfg["cast"]
        hold = ic.random_sleep.__func__ if False else None  # type hint hint

        # Draw back (hold left mouse) then release to cast
        hold_time = (
            cast_cfg["hold_seconds_min"]
            + (cast_cfg["hold_seconds_max"] - cast_cfg["hold_seconds_min"])
            * __import__("random").random()
        )
        ic.hold_mouse(cast_cfg["button"], hold_time)
        ic.safe_sleep(delays["after_cast_settle"])
        logger.info("Cast completed (held %.2fs).", hold_time)

    # ------------------------------------------------------------------
    # Phase: Wait for bite
    # ------------------------------------------------------------------

    def _wait_for_bite(self, cfg: dict, delays: dict) -> bool:
        """
        Wait for a fish bite, using memory detection when available.

        Memory path: polls get_state() until the state is "bite" (or equivalent
        as mapped by the scanner).  Zero false-positive risk.

        CV fallback: mean-abs-diff scene-change on the centre-screen region,
        with water-splash motion burst + template/colour as secondary checks.
        A 2-second warm-up seeds the baseline after casting to avoid triggering
        on the cast animation itself.

        Returns True on bite, False on timeout.
        """
        timeout  = cfg.get("max_bite_wait_seconds", 90)
        interval = delays["bite_poll_interval"]

        # ── Memory path ───────────────────────────────────────────────────
        reader = memory_reader.get_reader()
        if reader:
            self._status("Waiting for bite (memory)…")
            # State leaves "waiting" → "bite"
            hit = reader.wait_for_state("bite", timeout, stop_fn=ic.is_stopped)
            if hit:
                logger.info("Bite detected via memory state.")
                self._gui.set_confidence(1.0)
            else:
                logger.info("Bite timeout (memory).")
            return hit

        # ── CV fallback ───────────────────────────────────────────────────
        deadline = time.monotonic() + timeout

        thresholds    = cfg["thresholds"]
        region_cam    = cfg["regions"].get("motion_sample")
        region_bite   = cfg["regions"].get("bite_indicator")
        tmpl          = cfg["templates"].get("bite")
        color_cfg     = cfg["color_ranges"].get("bite")
        sc_threshold  = thresholds.get("bite_scene_change", 25.0)
        mot_threshold = thresholds["bite_motion"]

        self._status("Settling…")
        warmup_end = time.monotonic() + 2.0
        prev_cam = prev_bite = None
        while time.monotonic() < warmup_end:
            if ic.is_stopped():
                return False
            f = vision.grab_region(region_cam)
            if f is not None:
                prev_cam = f
            f = vision.grab_region(region_bite)
            if f is not None:
                prev_bite = f
            ic.safe_sleep(0.1)

        self._status("Waiting for bite (CV)…")
        poll_n = 0

        while time.monotonic() < deadline:
            if ic.is_stopped():
                return False
            while ic.is_paused():
                ic.safe_sleep(0.1)

            poll_n += 1

            curr_cam = vision.grab_region(region_cam)
            if curr_cam is not None and prev_cam is not None:
                hit, diff = vision.detect_bite_scene_change(
                    prev_cam, curr_cam, threshold=sc_threshold
                )
                self._gui.set_confidence(min(diff / max(sc_threshold, 1e-6), 1.0))
                if poll_n % 20 == 0:
                    logger.debug(
                        "Bite watch: scene_diff=%.1f  threshold=%.1f",
                        diff, sc_threshold,
                    )
                if hit:
                    logger.info(
                        "Bite! scene-change diff=%.1f (threshold %.1f)",
                        diff, sc_threshold,
                    )
                    return True
                prev_cam = curr_cam

            detected, conf, prev_bite = vision.detect_bite(
                region=region_bite,
                template_path=tmpl,
                prev_frame=prev_bite,
                color_cfg=color_cfg,
                template_threshold=thresholds["bite_template"],
                motion_threshold=mot_threshold,
                color_pixel_ratio=thresholds["color_pixel_ratio"],
            )
            if detected:
                self._gui.set_confidence(conf)
                logger.info("Bite! motion-burst conf=%.3f", conf)
                return True

            ic.safe_sleep(interval)

        return False

    # ------------------------------------------------------------------
    # Phase: Hook
    # ------------------------------------------------------------------

    def _hook(self, cfg: dict, delays: dict) -> None:
        """
        Send right-click at 0.5 s intervals until the fish is confirmed hooked,
        or max_hook_attempts is exhausted.

        Confirmation strategy:
          Memory: wait for state to change from "bite" → "fight" (or any non-bite state).
          CV: check phaseCorrelate magnitude > hook_confirm_motion after each click.
        """
        max_attempts   = cfg.get("max_hook_attempts", 8)
        retry_interval = delays.get("hook_retry_interval", 0.5)

        reader = memory_reader.get_reader()

        for attempt in range(1, max_attempts + 1):
            if ic.is_stopped():
                return

            self._status(f"Hooking… (attempt {attempt}/{max_attempts})")
            ic.click_mouse(cfg["hook_button"])
            logger.info("Hook click %d/%d sent.", attempt, max_attempts)

            ic.safe_sleep(retry_interval)

            if reader:
                state = reader.get_state()
                if state and state not in ("bite", "waiting", "idle"):
                    logger.info("Fish hooked — state now '%s' (attempt %d).", state, attempt)
                    self._status("Fish hooked!")
                    return
            else:
                region         = cfg["regions"].get("motion_sample")
                confirm_thresh = cfg["thresholds"].get("hook_confirm_motion", 1.5)
                prev_frame     = vision.grab_region(region)
                ic.safe_sleep(0.05)
                curr_frame = vision.grab_region(region)
                if curr_frame is not None and prev_frame is not None:
                    dx, dy    = vision.measure_camera_motion(prev_frame, curr_frame)
                    magnitude = (dx * dx + dy * dy) ** 0.5
                    logger.info(
                        "Hook check %d: motion=%.2f (need %.2f)",
                        attempt, magnitude, confirm_thresh,
                    )
                    if magnitude >= confirm_thresh:
                        logger.info("Fish hooked! (motion confirmed, attempt %d)", attempt)
                        self._status("Fish hooked!")
                        return

        logger.warning("Hook not confirmed after %d attempts — proceeding.", max_attempts)
        self._status("Hooking (unconfirmed)…")

    # ------------------------------------------------------------------
    # Phase: Fight
    # ------------------------------------------------------------------

    def _fight(self, cfg: dict, delays: dict) -> bool:
        """
        Counter the fish's movement until it tires out or the state changes.

        Memory path: keeps pressing the counter-WASD key while state == "fight";
        exits as soon as state transitions to "caught" or "idle".

        CV fallback: phaseCorrelate camera-motion direction → counter key.
        A grace period (min_fight_seconds) prevents the tired-check from
        triggering immediately after hooking before the camera starts moving.

        Returns True when the fish is ready to reel, False on timeout.
        """
        self._status("Fighting fish…")
        timeout   = cfg.get("max_fight_seconds", 180)
        min_fight = cfg.get("min_fight_seconds", 6.0)
        deadline  = time.monotonic() + timeout
        grace_end = time.monotonic() + min_fight
        interval  = delays["fight_frame_interval"]

        of_cfg         = cfg["optical_flow"]
        direction_keys = cfg["direction_keys"]
        region         = cfg["regions"]["motion_sample"]

        reader = memory_reader.get_reader()

        # ── Memory path ───────────────────────────────────────────────────
        if reader:
            prev_frame = vision.grab_region(region)

            while time.monotonic() < deadline:
                if ic.is_stopped():
                    return True

                while ic.is_paused():
                    ic.safe_sleep(0.1)

                state = reader.get_state()
                if state not in ("fight", None):
                    logger.info("Fight ended — state now '%s'.", state)
                    return True

                # Still fighting — read direction from camera and counter it
                ic.safe_sleep(interval)
                curr_frame = vision.grab_region(region)
                if curr_frame is None or prev_frame is None:
                    prev_frame = curr_frame
                    continue

                direction, magnitude, _ = vision.detect_fight_direction(
                    prev_frame,
                    curr_frame,
                    motion_threshold=of_cfg["motion_threshold"],
                    still_threshold=of_cfg["still_threshold"],
                    dominance_ratio=of_cfg["direction_dominance_ratio"],
                )
                self._gui.set_confidence(magnitude)
                prev_frame = curr_frame

                if direction:
                    key = direction_keys.get(direction)
                    if key:
                        self._status(f"Fighting — {direction} → {key}")
                        ic.tap_key(key, hold=delays["direction_key_hold"])

            return False

        # ── CV fallback ───────────────────────────────────────────────────
        prev_frame = vision.grab_region(region)
        if prev_frame is None:
            logger.warning("motion_sample region not set — cannot detect fight direction.")
            return False

        while time.monotonic() < deadline:
            if ic.is_stopped():
                return True

            while ic.is_paused():
                ic.safe_sleep(0.1)

            ic.safe_sleep(interval)
            curr_frame = vision.grab_region(region)
            if curr_frame is None:
                continue

            direction, magnitude, is_tired = vision.detect_fight_direction(
                prev_frame,
                curr_frame,
                motion_threshold=of_cfg["motion_threshold"],
                still_threshold=of_cfg["still_threshold"],
                dominance_ratio=of_cfg["direction_dominance_ratio"],
            )
            self._gui.set_confidence(magnitude)
            prev_frame = curr_frame

            in_grace = time.monotonic() < grace_end
            if is_tired:
                if in_grace:
                    logger.debug(
                        "Camera still (mag=%.2f) but in grace period — waiting.",
                        magnitude,
                    )
                    continue
                logger.info("Fish is tired (motion magnitude=%.3f).", magnitude)
                return True

            if direction:
                key = direction_keys.get(direction)
                if key:
                    self._status(f"Fighting — fish going {direction}, pressing {key}")
                    logger.debug(
                        "Fish direction=%s mag=%.2f → pressing %s",
                        direction, magnitude, key,
                    )
                    ic.tap_key(key, hold=delays["direction_key_hold"])
            else:
                logger.debug("Motion detected (mag=%.2f) but direction unclear.", magnitude)

        return False

    # ------------------------------------------------------------------
    # Phase: Reel
    # ------------------------------------------------------------------

    def _reel(self, cfg: dict, delays: dict) -> None:
        """Hold Space until the catch-complete state is detected."""
        self._status("Reeling in…")
        reel_key = cfg["reel_key"]
        ic.hold_key(reel_key)
        logger.info("Space held — reeling.")

        deadline = time.monotonic() + 45  # hard cap
        interval = delays["reel_poll_interval"]
        reader   = memory_reader.get_reader()

        try:
            while time.monotonic() < deadline:
                if ic.is_stopped():
                    break
                while ic.is_paused():
                    ic.safe_sleep(0.1)

                if reader:
                    state = reader.get_state()
                    if state == "caught":
                        logger.info("Catch complete (memory state=caught).")
                        self._gui.set_confidence(1.0)
                        break
                    ic.safe_sleep(interval)
                    continue

                # CV fallback
                caught, conf = vision.detect_catch_complete(
                    cfg["regions"]["catch_indicator"],
                    cfg["templates"].get("catch_complete"),
                    cfg["thresholds"]["catch_complete"],
                )
                self._gui.set_confidence(conf)
                logger.debug("Catch check: caught=%s conf=%.3f", caught, conf)

                if caught:
                    logger.info("Catch complete! conf=%.3f", conf)
                    break

                ic.safe_sleep(interval)
        finally:
            ic.release_key(reel_key)

    # ------------------------------------------------------------------
    # Phase: Post-catch — press F to stow the fish
    # ------------------------------------------------------------------

    def _post_catch(self, cfg: dict, delays: dict) -> None:
        """
        After reeling in, the player is holding the fish.
        Press F to put it away, then wait before the next cast.
        """
        self._status("Stowing fish…")
        ic.random_sleep(delays["after_catch_min"], delays["after_catch_max"])
        ic.tap_key(cfg["post_catch_key"], hold=0.08)
        logger.info("Pressed %s to stow fish.", cfg["post_catch_key"])
        ic.safe_sleep(0.5)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _status(self, text: str) -> None:
        logger.info(text)
        self._gui.set_status(text)


# ---------------------------------------------------------------------------
# Application bootstrap
# ---------------------------------------------------------------------------

def main() -> None:
    cfg = config.load()
    loop: Optional[FishingLoop] = None

    def on_start():
        nonlocal loop
        if loop and not ic.is_stopped():
            logger.warning("Loop already running.")
            return
        loop = FishingLoop(gui)
        loop.start()

    def on_stop():
        if loop:
            loop.stop()

    def on_pause():
        if loop:
            loop.toggle_pause()

    def on_calibrate():
        def _run():
            mgr = calibration.CalibrationManager(
                status_callback=lambda m: gui.set_status(m)
            )
            mgr.run_full_calibration()

        threading.Thread(target=_run, daemon=True).start()

    def on_stop_hotkey():
        on_stop()
        gui.set_status("EMERGENCY STOP")

    def on_pause_toggle(paused: bool):
        gui.set_paused(paused)

    hotkeys = ic.HotkeyListener(
        stop_key=cfg["hotkeys"]["emergency_stop"],
        pause_key=cfg["hotkeys"]["pause_resume"],
        on_stop=on_stop_hotkey,
        on_pause_toggle=on_pause_toggle,
    )
    hotkeys.start()

    gui = gui_module.FishingGUI(
        on_start=on_start,
        on_stop=on_stop,
        on_pause=on_pause,
        on_calibrate=on_calibrate,
    )

    logger.info("CrimmyFish started. Config: %s", config.CONFIG_PATH)
    try:
        gui.run()
    finally:
        memory_reader.release_reader()


if __name__ == "__main__":
    main()
