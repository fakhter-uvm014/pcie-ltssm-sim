# What to expect

This page describes what a healthy run looks like, phase by phase, with the
numbers the model produces. Use it as the "known good" picture before you
look at a faulty scenario. All numbers come from the model (scenario S01);
real hardware has different absolute times, but the order of events and
the shape of the counters carry over.

## A healthy run in one picture

```
RP  |--Detect.Quiet (12 ms)--|D|Poll|Cfg|L0 Gen1|Rec|  EQ 8G  |Rec|L0 Gen3|Rec|  EQ 16G |Rec|L0 Gen4 ...
    0                      12.0  12.07          12.10     12.90        12.92       13.73 ms
```

* One Detect pass, one Polling pass, one Configuration pass, then L0.
* Exactly one Recovery pass per speed step, with one equalization block at
  8 GT/s and one at 16 GT/s.
* After the final speed: nothing. A healthy link sits in L0.

## Phase by phase

| Phase | Typical duration in the model | Healthy trace shows | Suspicious if |
|---|---|---|---|
| Detect.Quiet | 12 ms (timer) | One `T01` per port, then `T03` with all lanes found ("lanes 0-15 (16 of 16)"). | `T04` "no receiver" repeating on a link that should be populated; fewer lanes found than both ends support. |
| Detect.Active | about 1 us | `T03` to Polling.Active. | A port that alternates Detect.Quiet / Detect.Active forever (`detect_cycles` keeps growing). |
| Polling.Active | about 64 us (1024 TS1 at 2.5 GT/s) | `T05` "sent 1024 TS1; received 8 consecutive TS1/TS2 on all N lanes". | Any Polling timeout (`T06`, `T07`, `T08`): lanes not hearing each other. |
| Polling.Configuration | about 2 us | `T10` "received 8 consecutive TS2". | Staying 48 ms and timing out (`T11`): the partner never sends TS2 (S08). |
| Configuration | about 8 us | `T12`-`T22`; `T15` names the width; `T21` names the partner's highest rate. | Width smaller than both ends support; a Configuration timeout (`T14`, `T23`). |
| L0 at Gen1 | about 20 us before the first speed step | `DL_Link_Active` status about 5 us after L0. | DL_Link_Active never rising (in real hardware: a data link layer problem). |
| Speed change Recovery | about 4 us at 2.5 GT/s, under 1 us at 8/16 GT/s | `T30` on the DSP (reason names the step and the cap), `T29` on the USP, `T44`, `T53`, `T56`. | A `T54` with "partner did not echo speed_change": the partner refused. |
| Equalization (per rate) | about 0.8 ms (Phase 2 and 3 each one 400 us preset trial) | `eq` rows with FOM above 60; status "Equalization 8.0 GT/s Complete+". | Several rejected presets; `T62`/`T65` "Phase N FAILED"; `rate ceiling lowered` (S04, S05). |
| L0 at the final speed | rest of the run | No more state changes on that link. | Recovery passes with reason "receiver error" (flaps, S07/S15). |

## Healthy counter values (S01)

| Link | First L0 | Final speed at | Recov | Flaps | Detect | Down | EQ try 8G/16G | EQ fail 8G/16G |
|---|---|---|---|---|---|---|---|---|
| A (RP-USP) | 12.075 ms | 13.726 ms | 2 | 0 | 0 | 0 | 1/1 | 0/0 |
| B (DSP1-EP1) | 24.077 ms | 25.728 ms | 2 | 0 | 1 | 0 | 1/1 | 0/0 |
| C (DSP2-EP2) | 24.077 ms | 25.728 ms | 2 | 0 | 1 | 0 | 1/1 | 0/0 |
| D (empty) | - | - | 0 | 0 | 3 | 0 | 0/0 | 0/0 |

How to read these:

* **Recovery count = number of speed steps** on a healthy link (2 with the
  default policy, 3 with `gen2_step: true`). Anything above that needs a
  cause.
* **Flaps should be 0.** A flap is a Recovery caused by an error.
* **Detect cycles of 1** on links B and C are normal: the endpoints looked
  for a receiver at 12 ms while their switch ports were still held waiting
  for link A. On an empty slot the count simply grows every 12 ms.
* **First L0 on B and C is about 12 ms after link A**: that is the switch
  downstream ports' own Detect.Quiet time after being released.
* **EQ tries of 1/1, failures 0/0.** Every additional try is a retry after
  a failure.

## Healthy vs suspicious numbers

| Quantity | Healthy | Worth a look | Very likely a problem |
|---|---|---|---|
| LnkSta speed vs both ends' LnkCap | equal to the lower LnkCap | lower, but LnkCtl2 Target Link Speed explains it (S16) | lower than both LnkCaps with EQ failures (S04, S05) |
| LnkSta width vs both ends' LnkCap | equal to the narrower LnkCap | narrower with a known slot/bifurcation reason | narrower than both with no explanation (S06) |
| flap_count | 0 | a few per day on real hardware, with AER correctable errors | many per second, or growing steadily (S07, S15) |
| recovery_causes | only "speed change" (and "L1 exit" with ASPM) | "retrain" (software asked) | "error" |
| eq_failures | 0 | 1 with a successful retry | equal to `eq_max_attempts` (rate given up) |
| link_down_count | 0 | 1 after a deliberate Hot Reset (S10) | any other link-down (S14) |
| detect_cycles on a populated link | 0-1 | a few during power sequencing | growing for the whole run (S09) |

## What the model will not show

* Real time scales for equalization (real links often spend longer), real
  eye margins, jitter, or bit error rates.
* Data traffic. L0 is assumed to carry data after the first few idle bursts;
  errors in L0 come from fault injection only.
* Transaction-layer effects of a link problem (completion timeouts, driver
  errors). The model stops at DL_Link_Active.

Next: [debug-guide.md](debug-guide.md) for what to do when the numbers
above do not match.
