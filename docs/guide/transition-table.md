# Transition table

This file is produced by `python -m ltssm table --markdown` from
`ltssm/transitions.py`. A test checks that it stays in sync.

Role: DSP = downstream-facing port (root port, switch downstream port);
USP = upstream-facing port (switch upstream port, endpoint).
Optional rows are not used by the built-in scenarios; unit tests cover them.

| Rule | From | Role | Event | When | To | Action | Why |
|---|---|---|---|---|---|---|---|
| T01 | Detect.Quiet | any | `quiet_timeout` | Detect.Quiet timer expired (12 ms) and the port is not held in reset | Detect.Active |  | A port waits in Detect.Quiet with its transmitter in Electrical Idle, then tries receiver detection. |
| T02 | Detect.Quiet | any | `rx_ei_exit` | a lane left Electrical Idle (the partner is already transmitting) and the port is not held | Detect.Active |  | If the partner is already sending, there is no need to wait for the quiet timer. |
| T03 | Detect.Active | any | `receiver_detected` | receiver termination seen on at least one lane | Polling.Active | use_detected_lanes | Somebody is connected: train on the lanes where a receiver was found. |
| T04 | Detect.Active | any | `no_receiver` | no receiver termination on any lane | Detect.Quiet | count_detect_cycle | Nobody answered (empty slot, card not powered or not seated, open lanes): wait and try again. |
| T05 | Polling.Active | any | `polling_active_done` | sent >= 1024 TS1 AND received 8 consecutive TS1/TS2 on every lane being trained | Polling.Configuration |  | Both sides hear each other on all lanes; move on to confirm with TS2. |
| T06 (opt) | Polling.Active | any | `polling_timeout_partial` | 24 ms timeout; some lanes received 8 TS1/TS2, others never did | Polling.Configuration | drop_silent_lanes | Train on the lanes that work and drop the rest; the link will come up narrower. |
| T07 (opt) | Polling.Active | any | `polling_timeout_silent` | 24 ms timeout; a receiver was detected but nothing was ever received (silent_partner_to_polling_compliance) | Polling.Compliance |  | A partner that terminates but never talks looks like a passive test load: send the Polling.Compliance test pattern. |
| T08 (opt) | Polling.Active | any | `polling_timeout` | 24 ms timeout and neither rule above applies | Detect.Quiet |  | Polling made no progress; start over from Detect. |
| T09 (opt) | Polling.Compliance | any | `rx_ei_exit` | the partner started transmitting (Electrical Idle exit) | Polling.Active |  | Stop sending the test pattern as soon as the partner wakes up. |
| T10 | Polling.Configuration | any | `polling_config_done` | received 8 consecutive TS2 on all lanes AND sent >= 16 TS2 after the first TS2 arrived | Configuration.Linkwidth.Start |  | Both sides confirmed with TS2; start configuring link and lane numbers. |
| T11 | Polling.Configuration | any | `polling_config_timeout` | 48 ms timeout without 8 consecutive TS2 on all lanes | Detect.Quiet |  | The partner never confirmed with TS2; give up and restart from Detect. |
| T12 | Configuration.Linkwidth.Start | DSP | `rx_link_number_echo` | DSP sends TS1 with its link number; 2 consecutive TS1 came back with that number on at least one lane | Configuration.Linkwidth.Accept |  | The DSP proposes a link number and waits for the USP to echo it. |
| T13 | Configuration.Linkwidth.Start | USP | `rx_link_number` | 2 consecutive TS1 with a non-PAD link number on at least one lane | Configuration.Linkwidth.Accept | adopt_link_number | The USP accepts the DSP's link number and starts echoing it. |
| T14 | Configuration.Linkwidth.Start | any | `linkwidth_start_timeout` | 24 ms timeout | Detect.Quiet |  | No usable link number exchange; restart from Detect. |
| T15 | Configuration.Linkwidth.Accept | DSP | `width_selected` | the link number was echoed; choose the widest supported width whose lanes 0..w-1 all echoed it | Configuration.Lanenum.Wait | assign_lane_numbers | Width is decided here: x16, x8, x4, x2 or x1 starting at lane 0. Missing lanes shrink the link. |
| T16 | Configuration.Linkwidth.Accept | USP | `rx_lane_numbers` | 2 consecutive TS1 with non-PAD lane numbers | Configuration.Lanenum.Wait | accept_lane_numbers | The USP learns its lane numbers; if lane 0 carries the highest number it applies lane reversal. |
| T17 | Configuration.Lanenum.Wait | DSP | `rx_lane_numbers_echo` | 2 consecutive TS1 with non-PAD lane numbers on every configured lane | Configuration.Lanenum.Accept |  | The USP is echoing lane numbers; check them. |
| T18 | Configuration.Lanenum.Wait | USP | `rx_lane_numbers_stable` | lane numbers stable on every configured lane for 2 consecutive TS1 | Configuration.Lanenum.Accept |  | The proposal is stable; accept it. |
| T19 | Configuration.Lanenum.Accept | DSP | `lane_numbers_match` | the echoed lane number on every configured lane matches what the DSP proposed | Configuration.Complete |  | Lane numbering agreed; confirm with TS2. |
| T20 | Configuration.Lanenum.Accept | USP | `rx_ts2` | 2 consecutive TS2 on every configured lane | Configuration.Complete |  | The DSP moved to Configuration.Complete; follow it. |
| T21 | Configuration.Complete | any | `cfg_complete_done` | received 8 consecutive TS2 AND sent >= 16 TS2 after the first one arrived | Configuration.Idle | record_partner_rates | Numbers confirmed and data rates exchanged (the partner's highest rate is remembered). |
| T22 | Configuration.Idle | any | `rx_idle_done` | received 8 consecutive Idle data symbols AND sent >= 16 Idle symbols after the first one arrived | L0 |  | Link is up at 2.5 GT/s. DL_Link_Active follows shortly after. |
| T23 (opt) | Configuration.Linkwidth.Accept<br>Configuration.Lanenum.Wait<br>Configuration.Lanenum.Accept<br>Configuration.Complete<br>Configuration.Idle | any | `config_timeout` | 2 ms timeout in a Configuration substate (simplified) | Detect.Quiet |  | Configuration stalled; restart from Detect. |
| T24 | L0 | DSP | `directed_hot_reset` | software asked for Hot Reset (Secondary Bus Reset) on this downstream port | Recovery.RcvrLock |  | Hot Reset is signaled with TS1s, so the DSP first goes through Recovery. |
| T25 (opt) | L0 | DSP | `directed_disable` | software set Link Disable | Recovery.RcvrLock |  | Link Disable is signaled with TS1s, so the DSP first goes through Recovery. |
| T26 (opt) | L0 | DSP | `directed_loopback` | a test asked for loopback | Recovery.RcvrLock |  | Loopback is requested with TS1s from Recovery. |
| T27 (opt) | L0 | DSP | `directed_retrain` | software set the Retrain Link bit | Recovery.RcvrLock | consume_retrain | Retrain Link forces a pass through Recovery (this is what 'setpci ... Retrain' does). |
| T28 | L0 | any | `receiver_error` | the receiver saw an error it cannot ride through (framing error, lost block alignment, loss of signal) | Recovery.RcvrLock | count_receiver_error | Errors in L0 send the link to Recovery to re-acquire lock. Each one is a 'flap'. |
| T29 | L0 | any | `rx_ts_in_l0` | 2 consecutive TS1/TS2 received while in L0 | Recovery.RcvrLock | follow_partner_into_recovery | Training sets in L0 mean the partner entered Recovery; follow it. |
| T30 | L0 | DSP | `speed_change_wanted` | both sides support a higher rate than the current one and the speed policy allows the next step | Recovery.RcvrLock | start_speed_change | A directed speed change: the DSP enters Recovery with the speed_change bit set in its TS1s. |
| T31 | L0 | USP | `aspm_l1_idle` | ASPM L1 enabled and the link has been idle for aspm_l1_idle_ns | L1.Entry | request_l1 | The upstream port asks to enter L1 (the PM DLLP handshake is symbolic in this model). |
| T32 | L0 | DSP | `partner_requested_l1` | the USP requested ASPM L1 | L1.Entry | ack_l1 | The DSP acknowledges and both sides send EIOS. |
| T33 (opt) | L0 | any | `aspm_l0s_idle` | ASPM L0s enabled and the transmitter has been idle for aspm_l0s_idle_ns | L0s.Entry |  | L0s is a transmit-side low-power state; the partner stays in L0 (simplified). |
| T34 (opt) | L0 | any | `directed_l23_ready` | the system prepared for power removal (PME_Turn_Off handshake, symbolic) | L2.Idle |  | L2 is the main-power-off path; minimal in this model. |
| T35 (opt) | L0s.Entry | any | `eios_sent` | EIOS sent and the transmitter settled in Electrical Idle | L0s.Idle |  | Transmitter is idle. |
| T36 (opt) | L0s.Idle | any | `l0s_traffic_pending` | there is data to send again | L0s.FTS |  | Wake the receiver with Fast Training Sequences. |
| T37 (opt) | L0s.FTS | any | `fts_sent` | N_FTS Fast Training Sequences sent | L0 |  | Back to L0 without a full Recovery. |
| T38 | L1.Entry | any | `l1_settled` | EIOS sent, EIOS received from the partner, and Electrical Idle settled | L1.Idle |  | Both transmitters are idle; the link is in L1. |
| T39 | L1.Idle | DSP | `traffic_pending` | the host has traffic for the device | Recovery.RcvrLock |  | Leaving L1 requires Recovery to re-acquire bit lock. |
| T40 | L1.Idle | any | `rx_ei_exit` | the partner left Electrical Idle (it is sending TS1) | Recovery.RcvrLock |  | The partner is waking the link; follow it into Recovery. |
| T41 (opt) | L2.Idle | any | `power_restored` | main power restored / wake event | Detect.Quiet |  | Coming back from L2 is a full retrain from Detect. |
| T42 | Recovery.RcvrLock | USP | `eq_required` | the rate just changed to 8.0 or 16.0 GT/s and equalization at this rate is not complete | Recovery.Equalization.Phase0 | begin_eq | The upstream port starts equalization in Phase 0. |
| T43 | Recovery.RcvrLock | DSP | `eq_required` | the rate just changed to 8.0 or 16.0 GT/s and equalization at this rate is not complete | Recovery.Equalization.Phase1 | begin_eq | The downstream port starts equalization in Phase 1 (Phase 0 belongs to the USP). |
| T44 | Recovery.RcvrLock | any | `rx_8_ts` | 8 consecutive TS1/TS2 on all configured lanes with matching link/lane numbers and speed_change bit | Recovery.RcvrCfg |  | Lock re-acquired; confirm configuration with TS2. |
| T45 (opt) | Recovery.RcvrLock | any | `lock_timeout_after_speed_change` | RcvrLock timeout right after a rate change | Recovery.Speed | speed_change_failed | The new rate does not work; go back to the previous rate. |
| T46 | Recovery.RcvrLock | any | `rcvrlock_timeout` | RcvrLock timeout (2 ms, simplified) with no lock | Detect.Quiet |  | Recovery failed: the link goes down (DL_Down) and retrains from Detect. |
| T47 | Recovery.RcvrCfg | DSP | `directed_hot_reset` | Hot Reset was requested; TS1s now carry the Hot Reset bit | Hot Reset |  | Directed Hot Reset (entry point simplified to RcvrCfg in this model). |
| T48 | Recovery.RcvrCfg | USP | `rx_hot_reset` | 2 consecutive TS1 with the Hot Reset bit | Hot Reset |  | The partner is resetting this link; follow it. |
| T49 (opt) | Recovery.RcvrCfg | DSP | `directed_disable` | Link Disable was requested | Disabled |  | Send TS1 with the Disable bit, then go idle. |
| T50 (opt) | Recovery.RcvrCfg | USP | `rx_disable` | 2 consecutive TS1 with the Disable bit | Disabled |  | The partner disabled the link. |
| T51 (opt) | Recovery.RcvrCfg | DSP | `directed_loopback` | loopback was requested (this port becomes loopback lead) | Loopback.Entry |  | Send TS1 with the Loopback bit. |
| T52 (opt) | Recovery.RcvrCfg | USP | `rx_loopback` | 2 consecutive TS1 with the Loopback bit | Loopback.Entry |  | Become the loopback follower. |
| T53 | Recovery.RcvrCfg | any | `rx_8_ts2_speed_change` | speed change requested by both sides: 8 consecutive TS2 with speed_change=1 AND >= 16 TS2 sent | Recovery.Speed | negotiate_new_rate | Both agree to change speed. The new rate is the highest rate both advertised. |
| T54 | Recovery.RcvrCfg | any | `rx_8_ts2` | 8 consecutive TS2 on all lanes AND >= 16 TS2 sent after the first one arrived | Recovery.Idle |  | Nothing else to do; return to L0 through Recovery.Idle. |
| T55 (opt) | Recovery.RcvrCfg | any | `rcvrcfg_timeout` | RcvrCfg timeout (2 ms, simplified) | Detect.Quiet |  | Recovery failed; the link goes down. |
| T56 | Recovery.Speed | any | `speed_ei_done` | electrical idle time elapsed and the speed change was negotiated | Recovery.RcvrLock | switch_to_new_rate | Switch to the new rate and re-acquire lock there. |
| T57 | Recovery.Speed | any | `speed_ei_done_revert` | electrical idle time elapsed after a failed speed change or failed equalization | Recovery.RcvrLock | revert_rate | Fall back to the rate that worked before. |
| T58 | Recovery.Equalization.Phase0 | USP | `rx_ec_01` | 2 consecutive TS1 with EC=01 on all lanes (the DSP is in Phase 1) | Recovery.Equalization.Phase1 |  | USP has lock with its starting preset; join Phase 1. |
| T59 | Recovery.Equalization.Phase1 | DSP | `rx_ec_01` | 2 consecutive TS1 with EC=01 on all lanes (the USP is in Phase 1) | Recovery.Equalization.Phase2 | mark_phase1_ok | Phase 1 done; the DSP moves to Phase 2 and waits for the USP's requests. |
| T60 | Recovery.Equalization.Phase1 | USP | `rx_ec_10` | 2 consecutive TS1 with EC=10 on all lanes (the DSP is in Phase 2) | Recovery.Equalization.Phase2 | mark_phase1_ok | Phase 1 done; the USP starts tuning the DSP transmitter. |
| T61 | Recovery.Equalization.Phase2 | USP | `preset_search_passed` | a requested DSP transmitter preset reached the figure-of-merit target | Recovery.Equalization.Phase3 | mark_phase2_ok | Downstream direction (DSP TX -> USP RX) is tuned. |
| T62 | Recovery.Equalization.Phase2 | USP | `preset_search_failed` | no preset reached the target within eq_max_presets_per_phase trials | Recovery.Speed | eq_failed | Equalization failed in Phase 2: go back to the previous rate. |
| T63 | Recovery.Equalization.Phase2 | DSP | `rx_ec_11` | 2 consecutive TS1 with EC=11 (the USP finished Phase 2) | Recovery.Equalization.Phase3 | mark_phase2_ok | The DSP now tunes the USP transmitter. |
| T64 | Recovery.Equalization.Phase3 | DSP | `preset_search_passed` | a requested USP transmitter preset reached the figure-of-merit target | Recovery.RcvrLock | eq_complete | Upstream direction tuned; equalization complete at this rate. |
| T65 | Recovery.Equalization.Phase3 | DSP | `preset_search_failed` | no preset reached the target within eq_max_presets_per_phase trials | Recovery.Speed | eq_failed | Equalization failed in Phase 3: go back to the previous rate. |
| T66 | Recovery.Equalization.Phase3 | USP | `rx_ec_00` | 2 consecutive TS1 with EC=00 (the DSP left equalization) | Recovery.RcvrLock | eq_complete | Equalization complete at this rate. |
| T67 | Recovery.Equalization.Phase2 | DSP | `partner_abandoned_eq` | EIOS received: the USP gave up on Phase 2 | Recovery.Speed | eq_failed | The responder follows the requester back to the previous rate. |
| T68 | Recovery.Equalization.Phase3 | USP | `partner_abandoned_eq` | EIOS received: the DSP gave up on Phase 3 | Recovery.Speed | eq_failed | The responder follows the requester back to the previous rate. |
| T69 (opt) | Recovery.Equalization.Phase0<br>Recovery.Equalization.Phase1<br>Recovery.Equalization.Phase2<br>Recovery.Equalization.Phase3 | any | `eq_phase_timeout` | phase timer expired (12 ms for phases 0/1, 24 ms for 2/3, simplified) | Recovery.Speed | eq_failed | A stuck equalization phase counts as an equalization failure. |
| T70 | Recovery.Idle | any | `rx_idle_done` | received 8 consecutive Idle data symbols AND sent >= 16 Idle symbols after the first one arrived | L0 |  | Back in L0, at whatever rate Recovery ended on. |
| T71 (opt) | Recovery.Idle | any | `recovery_idle_timeout` | Recovery.Idle timeout (2 ms, simplified) | Detect.Quiet |  | Recovery failed; the link goes down. |
| T72 | Hot Reset | any | `hot_reset_done` | 2 ms Hot Reset timer expired | Detect.Quiet |  | After Hot Reset the link retrains from Detect, starting again at 2.5 GT/s. |
| T73 (opt) | Disabled | DSP | `directed_enable` | software cleared Link Disable | Detect.Quiet |  | Re-enabled links retrain from Detect. |
| T74 (opt) | Disabled | USP | `rx_ei_exit` | the partner left Electrical Idle (anything other than TS1 with the Disable bit) | Detect.Quiet |  | The partner is training again; follow it. |
| T75 (opt) | Loopback.Entry | DSP | `rx_loopback_echo` | 2 consecutive TS1 with the Loopback bit came back | Loopback.Active |  | The follower is looping data back. |
| T76 (opt) | Loopback.Entry | USP | `loopback_entered` | loopback path set up | Loopback.Active |  | Start echoing received data. |
| T77 (opt) | Loopback.Active | DSP | `directed_loopback_exit` | the test finished | Loopback.Exit |  | The lead ends loopback with EIOS. |
| T78 (opt) | Loopback.Active | USP | `rx_eios` | EIOS received | Loopback.Exit |  | The lead ended loopback. |
| T79 (opt) | Loopback.Exit | any | `loopback_exit_done` | exit timer expired | Detect.Quiet |  | After loopback the link retrains from Detect. |

## What each substate transmits

| Substate | Transmits | Timer |
|---|---|---|
| Detect.Quiet | transmitter in Electrical Idle | `detect_quiet_ns` |
| Detect.Active | receiver-detection pulse on every lane | `detect_active_ns` |
| Polling.Active | TS1 with PAD link/lane numbers | `polling_active_ns` |
| Polling.Compliance | Polling.Compliance test pattern |  |
| Polling.Configuration | TS2 with PAD link/lane numbers | `polling_configuration_ns` |
| Configuration.Linkwidth.Start | DSP: TS1 with link number; USP: PAD | `cfg_linkwidth_start_ns` |
| Configuration.Linkwidth.Accept | TS1 with link number | `cfg_substate_timeout_ns` |
| Configuration.Lanenum.Wait | TS1 with link and lane numbers | `cfg_substate_timeout_ns` |
| Configuration.Lanenum.Accept | TS1 with link and lane numbers | `cfg_substate_timeout_ns` |
| Configuration.Complete | TS2 with link/lane numbers and supported rates | `cfg_substate_timeout_ns` |
| Configuration.Idle | Idle data | `cfg_substate_timeout_ns` |
| L0 | data/idle (only the first few bursts are simulated) |  |
| L0s.Entry | EIOS, then idle | `l1_entry_settle_ns` |
| L0s.Idle | transmitter in Electrical Idle | `l0s_residency_ns` |
| L0s.FTS | Fast Training Sequences |  |
| L1.Entry | EIOS, then idle | `l1_entry_settle_ns` |
| L1.Idle | both directions in Electrical Idle |  |
| L2.Idle | main power off (minimal) |  |
| Recovery.RcvrLock | TS1 (speed_change bit if directed) | `recovery_rcvrlock_ns` |
| Recovery.RcvrCfg | TS2 | `recovery_rcvrcfg_ns` |
| Recovery.Speed | EIOS, Electrical Idle, rate switch | `recovery_speed_ei_ns` |
| Recovery.Equalization.Phase0 | TS1 EC=00 with the starting preset | `eq_phase01_timeout_ns` |
| Recovery.Equalization.Phase1 | TS1 EC=01 | `eq_phase01_timeout_ns` |
| Recovery.Equalization.Phase2 | TS1 EC=10 with requested/applied preset | `eq_phase23_timeout_ns` |
| Recovery.Equalization.Phase3 | TS1 EC=11 with requested/applied preset | `eq_phase23_timeout_ns` |
| Recovery.Idle | Idle data | `recovery_idle_ns` |
| Hot Reset | TS1 with the Hot Reset bit | `hot_reset_ns` |
| Disabled | a few bursts of TS1 with the Disable bit, then Electrical Idle |  |
| Loopback.Entry | TS1 with the Loopback bit | `loopback_exit_ns` |
| Loopback.Active | looped-back data (represented by TS1 with the Loopback bit) |  |
| Loopback.Exit | EIOS | `loopback_exit_ns` |

