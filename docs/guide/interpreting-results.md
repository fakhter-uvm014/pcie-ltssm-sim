# Interpreting results

How to read every output of the simulator: the console summary, the
counters, the event log, the trace JSON, the viewer, the lspci excerpt and
the pass/fail checks.

## The console summary (`python -m ltssm run S04`)

| Block | What it tells you |
|---|---|
| Header | Scenario id, title, description, the injected faults or software commands ("Stimulus") and the expected outcome. |
| Run line | Topology, seed, simulated time and how many events were logged. |
| Final link status | One row per link: state, speed, width, DL_Active. A link is "up" only when both ends are in L0. |
| Per-port status | One row per port, like LnkSta plus LnkCap: state, speed, width, maximum speed/width, Target Link Speed, EQ complete bits at 8 and 16 GT/s (`EQ8`, `EQ16`) and the receiver error count. |
| Counters | Per link timing and event counts (see below). |
| Enumeration | The bus tree after training, and why missing devices are missing. |
| Checks | Each expected check from the scenario file with PASS/FAIL, then the global invariants. |
| What this means | One paragraph from the scenario file. |

The exit code is `0` when every check passes and `1` otherwise (`run-all`
returns `1` if any scenario fails), so the commands can be used in scripts
and CI.

## Counters

| Counter | Meaning | Healthy value |
|---|---|---|
| `time_to_l0_ns` (First L0) | Simulated time when both ends first reached L0. | about 12.1 ms for a link that trains at power-on |
| `time_to_final_speed_ns` (Final speed at) | Time when the link last reached L0 at a new speed (after a link-down it restarts). | about 1.7 ms after first L0 for Gen4 |
| `recovery_count` | Times the link left L0 (or L1) for Recovery, counted once per link per episode. | number of speed steps |
| `flap_count` | Recovery episodes caused by an error. | 0 |
| `recovery_causes` | Breakdown: `speed change`, `error`, `L1 exit`, `directed`, `retrain`. | only `speed change` |
| `speed_changes` | Successful rate switches. | 2 (Gen1 -> Gen3 -> Gen4) |
| `detect_cycles` | Failed receiver detections, summed over both ends. | 0-1 on a populated link |
| `link_down_count` | Times a link that was up went back to Detect, Hot Reset or Disabled. | 0 |
| `l1_entries` | ASPM L1 entries. | 0 unless ASPM is enabled |
| `rx_errors` | Receiver errors in L0 (both ends); a stand-in for AER Receiver Error. | 0 |
| `eq_attempts` / `eq_failures` | Equalization attempts and failures at 8 GT/s (Gen3) and 16 GT/s (Gen4). | 1 / 0 per rate |

## The event log (`--trace`)

```
  12.064488 ms  RP    [A]  Polling.Active -> Polling.Configuration  (Gen1 x16, T05)
                  why: sent 1024 TS1 (>= 1024); received 8 consecutive TS1/TS2 on all 16 lanes
```

* time, port, `[link]`, transition, the port's speed and width after the
  transition, and the transition-table row (`T05`; look it up in
  [transition-table.md](transition-table.md));
* `why:` is the condition that fired, with the numbers that satisfied it.

Other lines are `status:` (DL_Link_Active, equalization complete bits, rate
switches, a switch port being held or released, rate ceiling changes),
`eq:` (one equalization preset trial with its figure of merit) and
`FAULT:` (fault injection; hidden in mystery mode). Filter with
`--port DSP1,EP1`.

Reading tips:

* The port that starts a Recovery has the interesting reason; its partner
  usually says "received TS1 while in L0: the partner entered Recovery".
* Timeout reasons print the last ordered set received; that is often the
  whole diagnosis (S08: a TS1 where a TS2 was needed).
* A speed-change reason names the limit that set the target ("because
  partner advertised up to Gen2", "because Target Link Speed (LnkCtl2) is
  Gen3", "because rate ceiling Gen2 after failures").

## The trace JSON (`traces/<ID>.json`)

| Field | Content |
|---|---|
| `scenario` | Title, description, symptom, faults (text and spec), commands, expected outcome and checks, `what_to_look_for`, `what_this_means`, `likely_root_causes`, `how_to_debug`, `mystery_hints`, `mystery_cause`. |
| `topology` | Devices, ports (role, kind, capabilities, link) and links (physical lanes). |
| `seed`, `duration_ns`, `config` | Everything needed to reproduce the run. |
| `stats` | Number of trace events, state events and engine events. |
| `events` | The list described below. |
| `links` | Final state per link plus `counters` and the faults on it. |
| `ports` | Final Link Status per port: `state`, `speed`, `width`, `link_training`, `dl_link_active`, `max_speed`, `max_width`, `target_link_speed`, `lane_reversed`, `eq_8gt_complete`, `eq_16gt_complete`, per-phase `eq` bits, `rate_ceiling`, `rx_errors`, `held`. |
| `enumeration` | `functions` (BDF, header type, IDs, primary/secondary/subordinate), `missing`, `device_count`, `lspci_t`. |
| `result` | `passed` and `checks: [{name, passed, detail}]`. |

Each event:

```json
{"t_ns": 13725676, "kind": "state", "port": "RP", "link": "A",
 "from": "Recovery.RcvrLock", "to": "Recovery.RcvrCfg",
 "reason": "received 8 consecutive TS1/TS2 on all 16 lanes at Gen4",
 "speed": "Gen4", "width": "x16", "rule": "T44"}
```

`kind` is `state`, `status`, `eq` or `fault`. `detail` (optional) carries
extra data such as `lanes_detected`, the equalization trials, the
`flags` that changed, or `cause` (the injected fault; removed in mystery
mode). Times are integers; the same seed produces a byte-identical file.

## The viewer (`docs/index.html`)

* **Scenario picker and pill**: PASS means the run matched the expected
  outcome, not that the link is healthy (S04 passes because it falls back
  as expected).
* **Topology**: each link colored by the state of its two ports at the
  playhead, with speed and width. A dashed grey line has no link.
* **Replay**: Start / Prev / Play / Next / End step through state changes;
  the table lists every link at the playhead.
* **Timeline**: one swimlane per port, colored by state family (legend
  above it). Zoom with the buttons, the slider or Ctrl + wheel; drag or use
  the Pan slider to move; hover a bar for the substate, reason, speed,
  width and rule; click to move the playhead. Red triangles mark injected
  faults.
* **Event log**: filter by port, state family, kind and text (for example
  a rule id such as `T62`). Click a row to move the playhead there.
* **Summary**: LnkSta-like table (`*` marks values below the port's own
  capability, on every port), counters, enumeration tree, and every check.
* **Learning notes / Mystery mode**: mystery mode hides the title, fault,
  checks and notes; "Reveal" shows them.

## The lspci excerpt (`python -m ltssm lspci S04`)

```
02:00.0 PCI bridge: Switch Downstream Port 1   [port DSP1, link B]
        LnkCap:  Speed 16GT/s, Width x16, ASPM L1
        LnkCtl:  ASPM Disabled; RCB 64 bytes, LnkDisable- CommClk+
        LnkSta:  Speed 5GT/s, Width x16
                 TrErr- Train- SlotClk+ DLActive+ BWMgmt- ABWMgmt-
        LnkCtl2: Target Link Speed: 16GT/s
        LnkSta2: EqualizationComplete- EqualizationPhase1+
                 EqualizationPhase2- EqualizationPhase3- LinkEqualizationRequest+
```

It imitates the layout of `lspci -vvv` so you can practice reading the real
thing, but it is model output. Like pciutils 3.8+, it prints `(downgraded)`
only on upstream-facing ports (switch upstream ports, endpoints) when LnkSta
is below that port's LnkCap, and prints nothing when they match; on root and
downstream ports compare LnkSta with LnkCap yourself (here: 5GT/s against
16GT/s). pciutils 3.6-3.7 printed `(ok)`/`(downgraded)` on every port. Being
below a port's own LnkCap is expected when the partner is slower or
narrower. In the model `EqualizationComplete` stays clear after a failed
attempt; on hardware it may read + after an unsuccessful attempt, so read
the per-phase bits and LinkEqualizationRequest first. The
16 GT/s line (`Phy16Sta`) mirrors the 16.0 GT/s Physical Layer capability.
`CESta: RxErr` stands in for the AER correctable Receiver Error status, with
the model's error count.

## Checks and invariants

Scenario checks come from `expected.checks` in the YAML file (`link`,
`port`, `sequence`, `never`, `only`, `counter`, `reason`, `devices`; see
`ltssm/checks.py`). On top of them, every run is checked against global
invariants:

* no port enters L0 at 8.0 GT/s without 8 GT/s equalization complete;
* no port enters L0 at 16.0 GT/s without 16 GT/s equalization complete;
* 16.0 GT/s is never attempted before 8 GT/s equalization is complete;
* with `dsp_start: wait_for_upstream`, switch downstream ports never leave
  Detect.Quiet while link A is not up;
* every state transition matches a row of the transition table;
* event times never go backwards.

A FAIL in a check prints the actual value next to the expected one; a FAIL
in an invariant means the model itself broke a rule and is a bug.
