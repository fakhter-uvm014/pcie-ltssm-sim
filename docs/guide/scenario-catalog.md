# Scenario catalog

Every scenario with its stimulus, expected outcome and learning point. Run
`python -m ltssm explain <ID>` for the full notes (what to look for, likely
root causes on real hardware, how to debug), `python -m ltssm run <ID>` to
check it, and `python -m ltssm run <ID> --hidden` to practice diagnosing it.

S01 to S12 are the core set; S13 to S16 add a speed-policy comparison, a
link that goes down, a marginal lane and a software speed cap.

This file is produced by `python scripts/gen_scenario_catalog.py` from the
scenario files.

| ID | Title | Topology | Stimulus | Expected outcome |
|---|---|---|---|---|
| S01 | Clean Gen4 bring-up | basic | none (healthy) | Links A and B reach L0 at Gen4 x16, link C at Gen4 x4 (the NVMe SSD is an x4 device in an x8 slot), link D stays in Detect (empty slot), and enumeration finds 7 functions. |
| S02 | Mixed capability (Gen2-only endpoint) | basic | EP2 supports only up to Gen2 | Link C trains at Gen2 x4 with no equalization; links A and B reach Gen4. |
| S03 | Receiver not detected (empty slot) | basic | nothing plugged into the slot on link D | Link D cycles Detect.Quiet <-> Detect.Active and never reaches Polling; links A, B and C train normally. |
| S04 | Gen3 equalization failure falls back to Gen2 | basic | equalization at Gen3 on link B cannot pass phase 2 | Link B falls back to Gen2 x16 after two failed 8.0 GT/s equalization attempts (Phase 2); the reason is logged. Links A and C reach Gen4. |
| S05 | Gen4 equalization failure settles at Gen3 | basic | equalization at Gen4 on link B cannot pass phase 3 | Link B settles at Gen3 x16: 8 GT/s equalization complete, 16 GT/s equalization failed twice in Phase 3. Links A and C reach Gen4. |
| S06 | Lane failure (lanes 8-15 open on link A) | basic | lanes 8-15 of link A are open (no receiver termination, no signal) | Link A trains at Gen4 x8 instead of x16; links B and C are unaffected. |
| S07 | Link flapping (periodic errors in L0) | basic | 6 receiver errors injected on link B in L0, every 3 ms from 32 ms | Link B goes L0 -> Recovery -> L0 six times (flap_count = 6), stays at Gen4 x16 and never goes down. |
| S08 | Stuck in Polling (partner never sends TS2) | point_to_point | EP's transmitter sends TS1 where it should send TS2 | The root port times out of Polling.Configuration into Detect and never reaches Configuration or L0; enumeration finds only the root port. |
| S09 | Upstream link down (link A never trains) | basic | every lane of link A is open (no receiver termination, no signal) | Link A stays in Detect; the switch downstream ports never leave Detect.Quiet; enumeration finds only the root port. |
| S10 | Hot Reset from the host | basic | at 35 ms on RP, software: Hot Reset requested (Secondary Bus Reset) | All switch links retrain after the Hot Reset; the final state matches S01 (A and B at Gen4 x16, C at Gen4 x4, 7 functions). |
| S11 | ASPM L1 entry and exit | point_to_point | config aspm_l1 = true; config aspm_l1_cycles = 2 | The link goes L0 -> L1 -> Recovery -> L0 twice, with no errors and no speed change; final state L0 Gen4 x16. |
| S12 | Lane reversal | point_to_point | link A is wired with lanes in reverse order | Link A trains at Gen4 x16 with lane reversal applied by the endpoint. |
| S13 | Speed policy comparison (Gen2 step) | basic | config gen2_step = true | Every populated link visits L0 at Gen1, Gen2, Gen3 and Gen4 (three speed changes instead of two) and ends as in S01. |
| S14 | Recovery fails and the link goes down | basic | every lane of link B opens at 32 ms (card removed / cable unplugged) | Link B goes L0 -> Recovery.RcvrLock -> Detect and stays down; DL_Link_Active clears and enumeration no longer finds EP1. |
| S15 | Marginal lane causes recovery storms at Gen4 | basic | lane 5 of link B corrupts 0.1% of ordered sets at Gen4 only | Link B stays at Gen4 x16 but goes through Recovery many times (flap_count >= 10), every error reported on lane 5; the link never goes down. |
| S16 | Target Link Speed capped at Gen3 by software | basic | software set Target Link Speed (LnkCtl2) of DSP1 to Gen3 | Link B trains to Gen3 x16 with 8 GT/s equalization complete and never attempts 16 GT/s; links A and C reach Gen4. |

## Suggested learning order

1. **S01**, the healthy baseline: learn what normal looks like and keep it open for comparison.
2. **S13**: the same hardware with a different speed policy. Paths can differ while results match.
3. **S02** and **S16**: slower than expected, but nothing failed.
4. **S06** and **S12**: width and lane numbering.
5. **S04** and **S05**: equalization failures and fallback.
6. **S03**, **S09** and **S08**: links that never come up.
7. **S07**, **S15** and **S11**: recoveries and what causes them.
8. **S10** and **S14**: links going down, on purpose and by accident.
9. Mystery mode: `python -m ltssm quiz` until you get them right without the hints.

## S01: Clean Gen4 bring-up

No faults. The root port trains link A to the switch, the switch releases its downstream ports, and every populated link trains at 2.5 GT/s first, then steps to 8.0 GT/s (with equalization) and 16.0 GT/s (with equalization). Slot 3 is empty, so link D keeps looking for a receiver. Use this run as the healthy reference for every other scenario.

**Stimulus:** none (healthy)

**Expected:** Links A and B reach L0 at Gen4 x16, link C at Gen4 x4 (the NVMe SSD is an x4 device in an x8 slot), link D stays in Detect (empty slot), and enumeration finds 7 functions.

**Learning point:** A healthy link goes Detect -> Polling -> Configuration -> L0 at 2.5 GT/s, then uses Recovery to change speed. Equalization runs once per rate (8.0 and 16.0 GT/s) and must finish before the link returns to L0 at that rate. Zero flaps, zero equalization failures, and every link at the lower of its two ends' capabilities is what "healthy" looks like.

**Try:** `python -m ltssm run S01 --trace`

## S02: Mixed capability (Gen2-only endpoint)

Endpoint 2 only supports 5.0 GT/s (Gen2). Its TS1/TS2 ordered sets advertise rates up to Gen2, so the switch downstream port DSP2 never asks for 8 or 16 GT/s on link C. The other links are unaffected.

**Stimulus:** EP2 supports only up to Gen2

**Expected:** Link C trains at Gen2 x4 with no equalization; links A and B reach Gen4.

**Learning point:** A link runs at the highest rate both ends advertise. A speed below one port's own LnkCap is expected when the other side is slower; it is only a problem when the speed is lower than BOTH ends' capabilities (see S04, S05 and S16).

**Try:** `python -m ltssm run S02 --trace` or `python -m ltssm run S02 --hidden`

## S03: Receiver not detected (empty slot)

Nothing is plugged into the slot behind switch downstream port 3. Receiver detection never sees termination on any lane, so DSP3 cycles between Detect.Quiet and Detect.Active every 12 ms and never reaches Polling.

**Stimulus:** nothing plugged into the slot on link D

**Expected:** Link D cycles Detect.Quiet <-> Detect.Active and never reaches Polling; links A, B and C train normally.

**Learning point:** Receiver detection is an electrical test: the transmitter checks whether a terminated receiver is present on each lane. No termination means nothing is there (or nothing is powered), so the LTSSM does not even try to train. An empty slot looks exactly like this, and so does a card that is not seated or not powered.

**Try:** `python -m ltssm run S03 --trace` or `python -m ltssm run S03 --hidden`

## S04: Gen3 equalization failure falls back to Gen2

On link B, equalization at 8.0 GT/s cannot pass Phase 2: the endpoint (EP1, the requester in Phase 2) asks switch port DSP1 to try transmitter presets, but none reaches the figure-of-merit target. The model retries once, gives up on 8.0 GT/s and above, and falls back to the highest rate that still works: 5.0 GT/s.

**Stimulus:** equalization at Gen3 on link B cannot pass phase 2

**Expected:** Link B falls back to Gen2 x16 after two failed 8.0 GT/s equalization attempts (Phase 2); the reason is logged. Links A and C reach Gen4.

**Learning point:** A link that settles below the rate BOTH ends support, with failed equalization phases and an equalization request flag, points at the electrical channel at that rate, not at capabilities or software. Phase 2 is the downstream direction (switch transmitter -> endpoint receiver), so that direction is the first suspect. Falling back keeps the system usable but hides the problem unless someone compares LnkSta with LnkCap.

**Try:** `python -m ltssm run S04 --trace` or `python -m ltssm run S04 --hidden`

## S05: Gen4 equalization failure settles at Gen3

On link B, equalization at 8.0 GT/s passes, but equalization at 16.0 GT/s cannot pass Phase 3: switch port DSP1 (the requester in Phase 3) cannot find an endpoint transmitter preset that reaches the target. After two failed attempts the link stays at 8.0 GT/s.

**Stimulus:** equalization at Gen4 on link B cannot pass phase 3

**Expected:** Link B settles at Gen3 x16: 8 GT/s equalization complete, 16 GT/s equalization failed twice in Phase 3. Links A and C reach Gen4.

**Learning point:** Each rate has its own equalization. Passing at 8 GT/s and failing at 16 GT/s is common on channels with marginal loss: 16 GT/s has half the unit interval and much higher loss at its Nyquist frequency. Phase 3 is the upstream direction (endpoint transmitter -> switch receiver).

**Try:** `python -m ltssm run S05 --trace` or `python -m ltssm run S05 --hidden`

## S06: Lane failure (lanes 8-15 open on link A)

Lanes 8 to 15 of link A (root port to switch) are open: no receiver termination and no signal. Receiver detection only finds lanes 0-7, so the link trains on those lanes and comes up x8 at full speed.

**Stimulus:** lanes 8-15 of link A are open (no receiver termination, no signal)

**Expected:** Link A trains at Gen4 x8 instead of x16; links B and C are unaffected.

**Learning point:** Width is normally set in Configuration, from the lanes that work on both sides (on real links it can change later through Recovery -> Configuration, which the model does not do). Lanes that fail detection are never used. The link chooses the widest supported width (x16, x8, x4, x2, x1) that starts at lane 0, so losing upper lanes costs bandwidth but not the link. Losing lane 0 would be much worse (try it: change the fault to lanes "0").

**Try:** `python -m ltssm run S06 --trace` or `python -m ltssm run S06 --hidden`

## S07: Link flapping (periodic errors in L0)

After link B reaches 16 GT/s, a receiver error is injected every 3 ms (six in total), alternating between the endpoint's and the switch port's receiver. Each error sends the link from L0 into Recovery, which re-locks and returns to L0 at the same speed. The link never goes down, but it keeps stopping traffic for a short time.

**Stimulus:** 6 receiver errors injected on link B in L0, every 3 ms from 32 ms

**Expected:** Link B goes L0 -> Recovery -> L0 six times (flap_count = 6), stays at Gen4 x16 and never goes down.

**Learning point:** Recovery is the LTSSM's way to re-acquire lock without taking the link down. Occasional recoveries are survivable, but frequent ones cost bandwidth and latency and usually come with correctable errors in AER. A rising flap count is an early warning: the next step on a worse channel is a recovery that fails and a link that goes down (see S14).

**Try:** `python -m ltssm run S07 --trace` or `python -m ltssm run S07 --hidden`

## S08: Stuck in Polling (partner never sends TS2)

Point-to-point link. The endpoint's transmitter never sends TS2: every time its LTSSM wants to send TS2 it sends TS1 instead (a PHY or firmware defect). The root port hears TS1s, moves to Polling.Configuration, and waits for 8 consecutive TS2 that never come. After the 48 ms Polling.Configuration timeout it goes back to Detect and tries again.

**Stimulus:** EP's transmitter sends TS1 where it should send TS2

**Expected:** The root port times out of Polling.Configuration into Detect and never reaches Configuration or L0; enumeration finds only the root port.

**Learning point:** Polling.Active proves both sides can receive TS1. Polling.Configuration proves both sides moved on and confirmed with TS2. A port that reaches Polling.Configuration but never leaves it is hearing the partner but not the confirmation. On real hardware this points at the partner's state machine or transmitter, or at lanes that only work in one direction. A related outcome is Polling.Compliance: if a port detects a receiver but never receives anything at all, it may enter Polling.Compliance and send its test pattern instead (the model has that rule too).

**Try:** `python -m ltssm run S08 --trace` or `python -m ltssm run S08 --hidden`

## S09: Upstream link down (link A never trains)

Every lane of link A (root port to switch) is open. Neither end detects a receiver, so link A cycles in Detect forever. Because the switch holds its downstream ports until link A reaches L0 (dsp_start = wait_for_upstream), links B, C and D never start, and the endpoints only see receivers that are held in reset.

**Stimulus:** every lane of link A is open (no receiver termination, no signal)

**Expected:** Link A stays in Detect; the switch downstream ports never leave Detect.Quiet; enumeration finds only the root port.

**Learning point:** When everything behind a point in the hierarchy is missing, look at the first link above the missing devices. A switch cannot be enumerated if its upstream link is down, and a switch that waits for its upstream link will not train its downstream links either. Fixing link A fixes everything.

**Try:** `python -m ltssm run S09 --trace` or `python -m ltssm run S09 --hidden`

## S10: Hot Reset from the host

At 35 ms, with every link at Gen4, software sets the Secondary Bus Reset bit on the root port. The root port signals Hot Reset with TS1s, the switch upstream port follows, and the switch propagates the reset to its downstream ports. Every link goes down, retrains from Detect at 2.5 GT/s, equalizes again and ends exactly as in S01.

**Stimulus:** at 35 ms on RP, software: Hot Reset requested (Secondary Bus Reset)

**Expected:** All switch links retrain after the Hot Reset; the final state matches S01 (A and B at Gen4 x16, C at Gen4 x4, 7 functions).

**Learning point:** Hot Reset is an in-band reset carried by training sets. It resets everything below the port that issues it, so all links below retrain from scratch. A link that goes down because of a deliberate reset looks very similar to an unexpected link-down in the timeline; the difference is the software command (and Hot Reset state) right before it.

**Try:** `python -m ltssm run S10 --trace` or `python -m ltssm run S10 --hidden`

## S11: ASPM L1 entry and exit

Point-to-point link with ASPM L1 enabled. Once the link is at Gen4 and idle for 2 ms, the endpoint asks to enter L1 (the PM handshake is symbolic in the model) and both sides send EIOS. After 3 ms in L1 the host has traffic again, so the root port wakes the link. Leaving L1 always goes through Recovery. This happens twice.

**Stimulus:** config aspm_l1 = true; config aspm_l1_cycles = 2

**Expected:** The link goes L0 -> L1 -> Recovery -> L0 twice, with no errors and no speed change; final state L0 Gen4 x16.

**Learning point:** Not every Recovery is a problem. ASPM L1 trades exit latency for power: each exit costs a Recovery pass. Separate recoveries by cause before drawing conclusions; a recovery count without errors and with L1 entries next to it is usually power management working as configured. If a device misbehaves only with ASPM enabled, the L1 exit path is the first suspect.

**Try:** `python -m ltssm run S11 --trace` or `python -m ltssm run S11 --hidden`

## S12: Lane reversal

Point-to-point link wired with lanes in reverse order: root port lane 0 connects to endpoint lane 15, lane 1 to lane 14, and so on. During Configuration the endpoint receives lane number 15 on its physical lane 0, recognizes the reversal, and maps its lanes in reverse. The link trains at full width and speed.

**Stimulus:** link A is wired with lanes in reverse order

**Expected:** Link A trains at Gen4 x16 with lane reversal applied by the endpoint.

**Learning point:** Lane numbers are assigned in Configuration. Reversal is allowed so board designers can route lanes without crossing them. It is invisible in normal status registers, which is why it usually only shows up in debug traces. Lane reversal support is optional: if NEITHER side supports it, a reversed link may fail to train or come up narrower (for example x1), which is a classic board bring-up surprise.

**Try:** `python -m ltssm run S12 --trace` or `python -m ltssm run S12 --hidden`

## S13: Speed policy comparison (Gen2 step)

Same hardware as S01, but the speed-change policy is set to gen2_step: true. Instead of Gen1 -> Gen3 -> Gen4, each downstream port steps through every rate: Gen1 -> Gen2 -> Gen3 -> Gen4. The final result is identical; the path and the time to reach the final speed are not.

**Stimulus:** config gen2_step = true

**Expected:** Every populated link visits L0 at Gen1, Gen2, Gen3 and Gen4 (three speed changes instead of two) and ends as in S01.

**Learning point:** How a link climbs to its final rate is a policy choice (hardware autonomous behavior, firmware, or software through Target Link Speed and Retrain Link). In the model the downstream port always initiates speed changes; on real links either port may. The ending state is what LnkSta shows; the path is only visible in a trace. When comparing two platforms, a different path does not mean a problem.

**Try:** `python -m ltssm run S13 --trace`

## S14: Recovery fails and the link goes down

Link B is up at 16 GT/s when, at 32 ms, every lane opens (as if the card were pulled or lost power). The receivers see the signal disappear and enter Recovery, but Recovery.RcvrLock can never re-acquire lock. After the RcvrLock timeout both ends give up, the Data Link Layer goes down and the ports return to Detect, where no receiver answers any more.

**Stimulus:** every lane of link B opens at 32 ms (card removed / cable unplugged)

**Expected:** Link B goes L0 -> Recovery.RcvrLock -> Detect and stays down; DL_Link_Active clears and enumeration no longer finds EP1.

**Learning point:** Recovery is the last chance to keep a link up. If it cannot re-acquire lock, the LTSSM drops to Detect and the Data Link Layer reports the link down; software sees the device disappear. Whether the system survives depends on hot-plug and error-containment support (surprise removal, DPC), not on the LTSSM.

**Try:** `python -m ltssm run S14 --trace` or `python -m ltssm run S14 --hidden`

## S15: Marginal lane causes recovery storms at Gen4

Lane 5 of link B corrupts ordered sets and data now and then, but only at 16 GT/s (seeded random errors, 0.1 percent per ordered set). Training succeeds, equalization passes, and the link reaches Gen4. From then on receiver errors on lane 5 keep sending the link through Recovery.

**Stimulus:** lane 5 of link B corrupts 0.1% of ordered sets at Gen4 only

**Expected:** Link B stays at Gen4 x16 but goes through Recovery many times (flap_count >= 10), every error reported on lane 5; the link never goes down.

**Learning point:** When errors cluster on one lane and one rate, the problem is local: one lane of one channel lacks margin at that rate. Equalization can pass and the link can still be marginal, because equalization only has to meet a target once at training time. The fix is usually physical (connector, trace, cable), or reducing the speed as a workaround.

**Try:** `python -m ltssm run S15 --trace` or `python -m ltssm run S15 --hidden`

## S16: Target Link Speed capped at Gen3 by software

Both ends of link B support 16 GT/s, but software (for example a BIOS "PCIe speed" option) has set Target Link Speed in DSP1's Link Control 2 register to 8 GT/s. The downstream port never advertises more than 8 GT/s, so the link stops at Gen3 with no errors and no failed equalization.

**Stimulus:** software set Target Link Speed (LnkCtl2) of DSP1 to Gen3

**Expected:** Link B trains to Gen3 x16 with 8 GT/s equalization complete and never attempts 16 GT/s; links A and C reach Gen4.

**Learning point:** "Slower than both ends support" has two very different families of causes: something failed (equalization, signal integrity) or someone asked for it (Target Link Speed, BIOS options, power policy). Check the configuration before chasing signal integrity: it takes a minute and saves days.

**Try:** `python -m ltssm run S16 --trace` or `python -m ltssm run S16 --hidden`
