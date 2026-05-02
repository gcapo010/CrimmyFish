"""
main.py — Entry point and main fishing loop.

The fishing loop runs in a background daemon thread so the tkinter GUI stays
responsive.  State machine:

  IDLE -> CASTING -> WAITING_BITE -> HOOKING -> FIGHTING -> REELING -> CATCH_COMPLETE -> IDLE

All detections delegate to vision.py; all inputs delegate to input_controller.py.
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


# ---------------------------------------------------------------------------
# Fishing state machine
# ---------------------------------------------------------------------------

class State(Enum):
    IDLE = auto()
    CASTING = auto()
    WAITING_BITE = auto()
    HOOKING = auto()
    FIGHTING = auto()
    REELING = auto()
    CATCH_COMPLETE = auto()
    STOPPED = auto()


class FishingLoop:
    """
    Executes the full fishing cycle and reports status back to the GUI.
    Runs on a separate thread; the GUI is updated via callback functions.
    """

    def __init__(self, gui: "gui_module.FishingGUI"):
        self._gui = gui
        self._state = State.IDLE
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
        self._state = State.IDLE
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("Fishing loop started.")

    def stop(self) -> None:
        ic.set_stopped(True)
        self._state = State.STOPPED
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
            self._update_status("Idle — waiting to cast")
            ic.random_sleep(delays["loop_restart_min"], delays["loop_restart_max"])
            if ic.is_stopped():
                break

            # 1. Cast
            self._cast(cfg, delays)
            if ic.is_stopped():
                break

            # 2. Wait for bite
            bit = self._wait_for_bite(cfg, delays)
            if ic.is_stopped():
                break
            if not bit:
                logger.info("Bite timeout — recasting.")
                continue

            # 3. Hook
            self._hook(cfg, delays)
            if ic.is_stopped():
                break

            # 4. Fight
            timed_out = self._fight(cfg, delays)
            if ic.is_stopped():
                break
            if timed_out:
                logger.warning("Fight timed out — recasting.")
                continue

            # 5. Reel
            self._reel(cfg, delays)
            if ic.is_stopped():
                break

            # 6. Catch complete
            self._loop_count += 1
            self._gui.set_loop_count(self._loop_count)
            logger.info("Fish caught! Total: %d", self._loop_count)

        self._update_status("Stopped")

    # ------------------------------------------------------------------
    # Individual phase methods
    # ------------------------------------------------------------------

    def _cast(self, cfg: dict, delays: dict) -> None:
        self._update_status("Casting…")
        ic.tap_key(cfg["cast_key"], hold=0.07)
        ic.random_sleep(delays["after_cast_min"], delays["after_cast_max"])
        logger.info("Cast performed.")

    def _wait_for_bite(self, cfg: dict, delays: dict) -> bool:
        """Poll the bite indicator until detected or timeout. Returns True if bite detected."""
        self._update_status("Waiting for bite…")
        timeout = cfg.get("max_bite_wait_seconds", 60)
        deadline = time.monotonic() + timeout
        interval = delays["bite_poll_interval"]

        while time.monotonic() < deadline:
            if ic.is_stopped():
                return False
            while ic.is_paused():
                ic.safe_sleep(0.1)

            detected, conf = vision.detect_state(
                cfg["regions"]["bite_indicator"],
                cfg["templates"]["bite"],
                cfg["color_ranges"].get("bite"),
                cfg["thresholds"]["bite"],
                cfg["thresholds"]["color_pixel_ratio"],
            )
            self._gui.set_confidence(conf)
            logger.debug("Bite check: detected=%s conf=%.3f", detected, conf)

            if detected:
                logger.info("Bite detected! Confidence=%.3f", conf)
                return True

            ic.safe_sleep(interval)

        return False

    def _hook(self, cfg: dict, delays: dict) -> None:
        self._update_status("Hooking fish!")
        ic.click_mouse(cfg["hook_button"])
        ic.random_sleep(delays["after_hook_min"], delays["after_hook_max"])
        logger.info("Hook click sent.")

    def _fight(self, cfg: dict, delays: dict) -> bool:
        """
        Fish-fighting phase.

        Detects whether the fish is tired first; if not, reads direction and
        sends the counter key.  Returns True if the fight timed out.
        """
        self._update_status("Fighting fish…")
        timeout = cfg.get("max_fight_seconds", 120)
        deadline = time.monotonic() + timeout
        interval = delays["fight_poll_interval"]

        dir_templates = {
            d: cfg["templates"].get(f"direction_{d}")
            for d in ("left", "right", "up", "down")
        }
        dir_keys = cfg["direction_keys"]

        while time.monotonic() < deadline:
            if ic.is_stopped():
                return False
            while ic.is_paused():
                ic.safe_sleep(0.1)

            # Check tired state first — it takes priority
            tired, conf = vision.detect_state(
                cfg["regions"]["stamina_indicator"],
                cfg["templates"]["tired"],
                cfg["color_ranges"].get("tired"),
                cfg["thresholds"]["tired"],
                cfg["thresholds"]["color_pixel_ratio"],
            )
            self._gui.set_confidence(conf)

            if tired:
                logger.info("Fish is tired! Confidence=%.3f", conf)
                return False  # Proceed to reel

            # Detect direction and counter it
            frame = vision.grab_region(cfg["regions"]["direction_indicator"])
            direction, dir_conf = vision.detect_direction(
                frame, dir_templates, cfg["thresholds"]["direction"]
            )

            if direction:
                key = dir_keys.get(direction)
                if key:
                    logger.debug("Fish going %s (conf=%.3f) — pressing %s", direction, dir_conf, key)
                    self._update_status(f"Fighting — fish going {direction}")
                    ic.tap_key(key, hold=delays["direction_key_hold"])
            else:
                logger.debug("No direction detected — waiting.")

            ic.safe_sleep(interval)

        return True  # Timed out

    def _reel(self, cfg: dict, delays: dict) -> None:
        """Hold the reel key until the catch-complete indicator appears."""
        self._update_status("Reeling in…")
        reel_key = cfg["reel_key"]
        ic.hold_key(reel_key)
        logger.info("Reel key held.")

        # Poll for catch completion while the key is held
        timeout = 30  # seconds max for reeling
        deadline = time.monotonic() + timeout
        interval = delays.get("bite_poll_interval", 0.1)

        try:
            while time.monotonic() < deadline:
                if ic.is_stopped():
                    break
                while ic.is_paused():
                    ic.safe_sleep(0.1)

                caught, conf = vision.detect_state(
                    cfg["regions"]["catch_indicator"],
                    cfg["templates"]["catch_complete"],
                    cfg["color_ranges"].get("catch_complete"),
                    cfg["thresholds"]["catch_complete"],
                    cfg["thresholds"]["color_pixel_ratio"],
                )
                self._gui.set_confidence(conf)
                logger.debug("Catch check: caught=%s conf=%.3f", caught, conf)

                if caught:
                    logger.info("Catch complete! Confidence=%.3f", conf)
                    break

                ic.safe_sleep(interval)
        finally:
            # Always release the reel key, even on error
            ic.release_key(reel_key)

        ic.random_sleep(delays["after_reel_min"], delays["after_reel_max"])

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _update_status(self, text: str) -> None:
        logger.info(text)
        self._gui.set_status(text)


# ---------------------------------------------------------------------------
# Application bootstrap
# ---------------------------------------------------------------------------

def main() -> None:
    # Load config early so hotkeys are available before the loop starts
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
        """Run calibration in a thread so the GUI stays live."""
        def _run():
            mgr = calibration.CalibrationManager(status_callback=lambda m: gui.set_status(m))
            gui.set_status("Calibrating regions…")
            mgr.calibrate_regions()
            gui.set_status("Regions done — capturing templates…")
            mgr.calibrate_templates()
            gui.set_status("Calibration complete.")

        t = threading.Thread(target=_run, daemon=True)
        t.start()

    def on_stop_hotkey():
        on_stop()
        gui.set_status("EMERGENCY STOP")

    def on_pause_toggle(paused: bool):
        gui.set_paused(paused)

    # Hotkey listener (daemon thread)
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
