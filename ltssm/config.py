"""Timeouts, counts and policy options for the model.

Author: Farzana Akhter

Every number here is SIMPLIFIED. Several follow the order of magnitude that
textbooks quote for real links (12 ms in Detect.Quiet, 24 ms in
Polling.Active, 48 ms in Polling.Configuration), others are shortened so that
runs stay small (Recovery timeouts are 2 ms here; textbooks quote 24 ms and
48 ms). None of them come from the PCI-SIG specification text, and none of
them should be used to judge real hardware.

All times are integers in nanoseconds so traces stay byte-identical across
machines (no floating point in the trace).

A scenario can override any field with a ``config:`` mapping, for example::

    config:
      gen2_step: true
      dsp_start: together
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Any, Dict, List

NS = 1
US = 1_000
MS = 1_000_000

# ----------------------------------------------------------------------------
# Data rates
# ----------------------------------------------------------------------------
GEN_NAMES = {1: "Gen1", 2: "Gen2", 3: "Gen3", 4: "Gen4"}
RATE_GTS = {1: "2.5 GT/s", 2: "5.0 GT/s", 3: "8.0 GT/s", 4: "16.0 GT/s"}
LSPCI_SPEED = {1: "2.5GT/s", 2: "5GT/s", 3: "8GT/s", 4: "16GT/s"}
ENCODING = {1: "8b/10b", 2: "8b/10b", 3: "128b/130b", 4: "128b/130b"}

# Time to send one ordered set (16 symbols), in picoseconds.
#   8b/10b:    16 symbols x 10 bits = 160 UI
#   128b/130b: 16 symbols x 8 bits + 2-bit sync header = 130 UI
OS_TIME_PS = {
    1: 160 * 400,  # 2.5 GT/s -> 400 ps per UI -> 64 ns
    2: 160 * 200,  # 5.0 GT/s -> 200 ps per UI -> 32 ns
    3: 130 * 125,  # 8.0 GT/s -> 125 ps per UI -> 16.25 ns
    4: 130 * 125 // 2,  # 16 GT/s -> 62.5 ps per UI -> 8.125 ns
}

WIDTHS = (16, 8, 4, 2, 1)


def parse_gen(value: Any) -> int:
    """Accept 1..4, "Gen3", "gen3", "8GT/s", "8.0 GT/s" and return 1..4."""
    if isinstance(value, int) and 1 <= value <= 4:
        return value
    text = str(value).strip().lower().replace(" ", "")
    if text.startswith("gen") and text[3:].isdigit():
        n = int(text[3:])
        if 1 <= n <= 4:
            return n
    for gen, gts in RATE_GTS.items():
        if text in (gts.lower().replace(" ", ""), LSPCI_SPEED[gen].lower()):
            return gen
    table = {"2.5": 1, "5": 2, "5.0": 2, "8": 3, "8.0": 3, "16": 4, "16.0": 4}
    if text in table:
        return table[text]
    raise ValueError(f"cannot parse link speed {value!r}; use Gen1..Gen4")


def parse_width(value: Any) -> int:
    """Accept 16, "x16", "X8" and return the lane count."""
    text = str(value).strip().lower().lstrip("x")
    n = int(text)
    if n not in WIDTHS:
        raise ValueError(f"unsupported width {value!r}; use x1, x2, x4, x8 or x16")
    return n


def parse_lanes(value: Any) -> List[int]:
    """Accept [8, 9], "8-15", "0,2,4-5" and return a sorted list of lanes."""
    if isinstance(value, int):
        return [value]
    if isinstance(value, (list, tuple)):
        out: List[int] = []
        for item in value:
            out.extend(parse_lanes(item))
        return sorted(set(out))
    lanes: List[int] = []
    for part in str(value).split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            lanes.extend(range(int(lo), int(hi) + 1))
        else:
            lanes.append(int(part))
    return sorted(set(lanes))


def format_lanes(lanes: List[int]) -> str:
    """[0,1,2,3,8] -> "0-3,8" (used in reasons and summaries)."""
    if not lanes:
        return "none"
    lanes = sorted(lanes)
    parts = []
    start = prev = lanes[0]
    for lane in lanes[1:]:
        if lane == prev + 1:
            prev = lane
            continue
        parts.append(f"{start}-{prev}" if prev != start else f"{start}")
        start = prev = lane
    parts.append(f"{start}-{prev}" if prev != start else f"{start}")
    return ",".join(parts)


def fmt_ms(t_ns: int) -> str:
    """Format nanoseconds as milliseconds with 3 decimals (console output)."""
    return f"{t_ns / MS:.3f} ms"


@dataclass
class Config:
    """All tunable numbers of the model. Values are simplified (see module doc)."""

    # --- Ordered-set exchange (how the engine batches traffic) ---------------
    os_per_burst: int = 32  # ordered sets per lane in one burst event
    burst_batch_after: int = 16  # after this many bursts in one substate ...
    burst_batch_factor: int = 8  # ... bursts are batched this many times larger (same content, fewer events)
    link_delay_ns: int = 10  # flight time through the channel (simplified)
    rx_consecutive_required: int = 8  # "8 consecutive TS1/TS2" style rules
    rx_consecutive_short: int = 2  # "2 consecutive TS1" style rules

    # --- Detect ---------------------------------------------------------------
    detect_quiet_ns: int = 12 * MS  # simplified; textbooks quote 12 ms
    detect_active_ns: int = 1 * US  # receiver-detection pulse (simplified)

    # --- Polling --------------------------------------------------------------
    polling_active_ns: int = 24 * MS  # simplified; textbooks quote 24 ms
    polling_active_min_ts1_tx: int = 1024  # TS1s sent before leaving Polling.Active
    polling_configuration_ns: int = 48 * MS  # simplified; textbooks quote 48 ms
    polling_cfg_min_ts2_tx_after_rx: int = 16  # TS2s sent after first TS2 received
    silent_partner_to_polling_compliance: bool = True  # Polling.Active timeout rule (T07)

    # --- Configuration --------------------------------------------------------
    cfg_linkwidth_start_ns: int = 24 * MS  # simplified
    cfg_substate_timeout_ns: int = 2 * MS  # simplified; other Configuration substates
    cfg_complete_min_ts2_tx_after_rx: int = 16
    idle_min_tx_after_rx: int = 16  # Idle data symbols sent after first received

    # --- L0 -------------------------------------------------------------------
    dl_active_delay_ns: int = 5 * US  # DL_Link_Active rises this long after L0 (timed flag)
    l0_idle_bursts: int = 4  # idle bursts sent after entering L0 (then data is assumed)
    speed_change_delay_ns: int = 20 * US  # DSP waits this long in L0 before the next speed step
    l0_error_check_ns: int = 50 * US  # how often lane error faults are sampled in L0

    # --- Recovery -------------------------------------------------------------
    recovery_rcvrlock_ns: int = 2 * MS  # simplified; textbooks quote 24 ms
    recovery_rcvrcfg_ns: int = 2 * MS  # simplified; textbooks quote 48 ms
    recovery_idle_ns: int = 2 * MS  # simplified
    recovery_speed_ei_ns: int = 2 * US  # electrical idle while the rate switches (simplified)
    rcvrcfg_min_ts2_tx_after_rx: int = 16

    # --- Speed change policy --------------------------------------------------
    gen2_step: bool = False  # True: Gen1 -> Gen2 -> Gen3 -> Gen4; False: Gen1 -> Gen3 -> Gen4
    eq_max_attempts: int = 2  # failed EQ attempts at a rate before the DSP gives up on it

    # --- Equalization (8.0 and 16.0 GT/s) -------------------------------------
    eq_phase01_timeout_ns: int = 12 * MS  # simplified
    eq_phase23_timeout_ns: int = 24 * MS  # simplified
    eq_preset_eval_ns: int = 400 * US  # time to try one preset and judge the eye (simplified)
    eq_max_presets_per_phase: int = 6  # presets a requester tries before declaring failure
    eq_fom_target: int = 60  # symbolic figure of merit a preset must reach (0..100)
    eq_search_order: List[str] = field(
        default_factory=lambda: ["P7", "P8", "P6", "P5", "P9", "P4", "P3", "P2", "P1", "P0", "P10"]
    )

    # --- Hot Reset / Disabled / Loopback (minimal) ----------------------------
    hot_reset_ns: int = 2 * MS  # simplified; textbooks quote 2 ms
    loopback_exit_ns: int = 2 * US
    disabled_ts1_bursts: int = 4  # bursts of TS1 with the Disable bit before going idle

    # --- ASPM (minimal) -------------------------------------------------------
    aspm_l1: bool = False  # endpoints request L1 after the link is idle
    aspm_l1_idle_ns: int = 2 * MS  # idle time before the USP asks for L1 (simplified)
    aspm_l1_cycles: int = 2  # how many L1 entries the model performs, then stays in L0
    l1_entry_settle_ns: int = 2 * US
    l1_residency_ns: int = 3 * MS  # time in L1.Idle before the host has traffic again
    aspm_l0s: bool = False  # L0s (transmit side only, minimal)
    aspm_l0s_idle_ns: int = 1 * MS
    l0s_residency_ns: int = 200 * US
    n_fts: int = 64  # FTS ordered sets sent to leave L0s

    # --- Switch ---------------------------------------------------------------
    dsp_start: str = "wait_for_upstream"  # or "together"

    # --- Simulation -----------------------------------------------------------
    duration_ns: int = 60 * MS  # default simulated time per scenario

    # -------------------------------------------------------------------------
    def burst_ns(self, gen: int) -> int:
        """Duration of one burst of ``os_per_burst`` ordered sets at ``gen``."""
        return self.os_per_burst * OS_TIME_PS[gen] // 1000

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def with_overrides(self, overrides: Dict[str, Any] | None) -> "Config":
        """Return a copy with scenario overrides applied.

        Keys ending in ``_ms`` or ``_us`` are converted to the matching
        ``_ns`` field, so scenario files can say ``detect_quiet_ms: 12``.
        """
        data = self.to_dict()
        names = {f.name for f in fields(self)}
        for key, value in (overrides or {}).items():
            target, scale = key, 1
            if key.endswith("_ms"):
                target, scale = key[:-3] + "_ns", MS
            elif key.endswith("_us"):
                target, scale = key[:-3] + "_ns", US
            if target not in names:
                raise KeyError(f"unknown config option {key!r}")
            if scale != 1:
                value = int(round(float(value) * scale))
            data[target] = value
        cfg = Config(**data)
        if cfg.dsp_start not in ("wait_for_upstream", "together"):
            raise ValueError("dsp_start must be 'wait_for_upstream' or 'together'")
        return cfg
