"""
main.py — Entry point and fishing state machine.

Fishing loop states:
  IDLE → CASTING → WAITING_BITE → HOOKING → FIGHTING → REELING → POST_CATCH → IDLE

Key mechanics based on Crimson Desert's actual fishing system:
  - Cast:      Hold left mouse button (0.8–1.2 s) then release
  - Bite:      Detected via water-splash motion burst in the cast-area region,
               with optional template match overlay
  - Hook:      Right mouse click
  - Fight:     Camera-motion direction via phaseCorrelate; counter with opposite WASD key
  - Tired:     Camera motion drops below still_threshold → fish is tired
  - Reel:      Hold Space until catch-complete template detected
  - Post-catch: Press F to stow the fish, then restart
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
        Poll for a fish bite.

        Primary detector — scene-change on motion_sample (centre screen):
          When a fish bites, Crimson Desert pitches the camera sharply
          downward.  The horizon rises from ~30 % to ~10 % of the frame
          and water fills the majority of the screen.  Mean-abs-diff on
          the centre region typically jumps to 25–60 at bite time versus
          a 1–5 baseline during idle ripple.  phaseCorrelate is NOT used
          here because the angular change is large enough to alias.

        Fallback — splash motion burst on bite_indicator:
          Catches the water disturbance if the scene-change threshold is
          set higher than needed.

        Template matching and colour fallback are also tried if configured.
        Returns True on bite, False on timeout.
        """
        self._status("Waiting for bite…")
        timeout  = cfg.get("max_bite_wait_seconds", 90)
        deadline = time.monotonic() + timeout
        interval = delays["bite_poll_interval"]

        thresholds     = cfg["thresholds"]
        region_cam     = cfg["regions"].get("motion_sample")
        region_bite    = cfg["regions"].get("bite_indicator")
        tmpl           = cfg["templates"].get("bite")
        color_cfg      = cfg["color_ranges"].get("bite")
        sc_threshold   = thresholds.get("bite_scene_change", 25.0)
        mot_threshold  = thresholds["bite_motion"]

        prev_cam  = vision.grab_region(region_cam)
        prev_bite = vision.grab_region(region_bite)

        while time.monotonic() < deadline:
            if ic.is_stopped():
                return False
            while ic.is_paused():
                ic.safe_sleep(0.1)

            # ── Primary: dramatic scene change (camera pitch at bite) ──────
            curr_cam = vision.grab_region(region_cam)
            if curr_cam is not None and prev_cam is not None:
                hit, diff = vision.detect_bite_scene_change(
                    prev_cam, curr_cam, threshold=sc_threshold
                )
                self._gui.set_confidence(min(diff / max(sc_threshold, 1e-6), 1.0))
                if hit:
                    logger.info("Bite! scene-change diff=%.1f (threshold %.1f)", diff, sc_threshold)
                    return True
                prev_cam = curr_cam

            # ── Fallback: water-splash frame-diff + template + colour ──────
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
        # Wait a moment after bite before clicking — lets the animation settle
        # so the right-click registers as a hook rather than being dropped.
        pre_wait = (
            delays.get("pre_hook_wait_min", 0.8)
            + (delays.get("pre_hook_wait_max", 1.2) - delays.get("pre_hook_wait_min", 0.8))
            * __import__("random").random()
        )
        self._status(f"Bite! Hooking in {pre_wait:.1f}s…")
        ic.safe_sleep(pre_wait)
        ic.click_mouse(cfg["hook_button"])
        ic.random_sleep(delays["after_hook_min"], delays["after_hook_max"])
        logger.info("Hook click sent (pre-wait %.2fs).", pre_wait)

    # ------------------------------------------------------------------
    # Phase: Fight
    # ------------------------------------------------------------------

    def _fight(self, cfg: dict, delays: dict) -> bool:
        """
        Read camera motion via phaseCorrelate to determine which way the fish
        is pulling, then press the opposite WASD key.

        When the camera stops moving (magnitude < still_threshold) the fish is
        tired and we return True to proceed to reeling.
        Returns False only on timeout.
        """
        self._status("Fighting fish…")
        timeout = cfg.get("max_fight_seconds", 180)
        deadline = time.monotonic() + timeout
        interval = delays["fight_frame_interval"]

        of_cfg = cfg["optical_flow"]
        direction_keys = cfg["direction_keys"]
        region = cfg["regions"]["motion_sample"]

        prev_frame = vision.grab_region(region)
        if prev_frame is None:
            logger.warning("motion_sample region not set — cannot detect fight direction.")
            return False

        while time.monotonic() < deadline:
            if ic.is_stopped():
                return True  # Clean exit, not a timeout

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

            if is_tired:
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

        return False  # Timed out

    # ------------------------------------------------------------------
    # Phase: Reel
    # ------------------------------------------------------------------

    def _reel(self, cfg: dict, delays: dict) -> None:
        """Hold Space until the catch-complete template is detected."""
        self._status("Reeling in…")
        reel_key = cfg["reel_key"]
        ic.hold_key(reel_key)
        logger.info("Space held — reeling.")

        deadline = time.monotonic() + 45  # hard cap
        interval = delays["reel_poll_interval"]

        try:
            while time.monotonic() < deadline:
                if ic.is_stopped():
                    break
                while ic.is_paused():
                    ic.safe_sleep(0.1)

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
            mgr = calibration.CalibrationManager(status_callback=lambda m: gui.set_status(m))
            gui.set_status("Calibrating regions…")
            mgr.calibrate_regions()
            gui.set_status("Regions done — capturing templates…")
            mgr.calibrate_templates()
            gui.set_status("Calibration complete.")

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
    gui.run()


if __name__ == "__main__":
    main()
