# Checking against real hardware

The simulator's outputs are named after the registers you can read on a
real system, so you can compare the model with a machine in front of you.
This page shows where those values live on Linux and Windows and how to
interpret them. Register layouts and tool output vary between pciutils
versions, kernels, operating systems and devices; check the documentation
for your versions before relying on an exact field name.

> **Caution.** Reading is safe. Writing configuration registers (`setpci`
> writes, retraining, resets) can hang a system, take a storage device
> offline or corrupt data in flight. Only write on a test system you can
> afford to reboot, as root, to the **downstream port** (root port or
> switch downstream port) above the device, and never while a filesystem
> on that device is mounted.

## Find the device and its parent port

```sh
lspci -tv                         # tree view: which bridge is above which device
lspci -s 0000:03:00.0 -vvv        # one device in detail (use sudo to see capabilities)
lspci -PP -s 03:00.0              # path of bridges from the root to the device (recent pciutils)
```

In the tree, every bridge shows `[secondary-subordinate]` bus numbers, the
same way the model's enumeration does. The port that trains a device's link
is the bridge directly above it (a root port or a switch downstream port).

## Fields in `lspci -vvv` (PCI Express capability)

| Field | What it shows | Model equivalent |
|---|---|---|
| `LnkCap: Port #N, Speed 16GT/s, Width x16, ASPM ...` | What this port can do (maximum speed and width, ASPM support, exit latencies). | `max_speed`, `max_width` |
| `LnkCtl: ASPM Disabled; RCB 64 bytes, LnkDisable- CommClk+ ...` | Control bits: ASPM enable, Read Completion Boundary, Link Disable, Common Clock. (Retrain Link, on downstream ports, is a write-only action and is not shown as a status.) | `aspm_l1`, commands `disable`, `retrain` |
| `LnkSta: Speed 8GT/s (downgraded), Width x8 (downgraded)` and, on the next line, `TrErr- Train- SlotClk+ DLActive+ BWMgmt- ABWMgmt-` | Current Link Speed and Negotiated Link Width; Link Training (set while in Configuration or Recovery); Data Link Layer Link Active; Link Bandwidth Management / Autonomous Bandwidth status (they report speed/width changes, not every Recovery). pciutils 3.8+ appends `(downgraded)` only on upstream-facing ports (switch upstream ports, endpoints) when LnkSta is below LnkCap and prints nothing when equal; pciutils 3.6-3.7 printed `(ok)`/`(downgraded)` on every port. On root and downstream ports, compare with LnkCap yourself. | `speed`, `width`, `link_training`, `dl_link_active` |
| `LnkCap2: Supported Link Speeds: 2.5-16GT/s, ...` | Every rate the port supports. | `max_speed` (the model supports all rates up to it) |
| `LnkCtl2: Target Link Speed: 16GT/s, ...` | Upper limit for the link speed set by software (meaningful on downstream ports). | `target_link_speed` (S16) |
| `LnkSta2: Current De-emphasis Level: ..., EqualizationComplete+ EqualizationPhase1+ EqualizationPhase2+ EqualizationPhase3+ LinkEqualizationRequest-` | 8.0 GT/s equalization status bits. After an unsuccessful attempt, Complete may still read + on some implementations; look at the phase bits and LinkEqualizationRequest first. | `eq.Gen3` (`eq_8gt_complete` and phase bits) |
| `Capabilities: [...] Physical Layer 16.0 GT/s` with `Phy16Sta: EquComplete+ EquPhase1+ ...` | 16.0 GT/s equalization status (decoded by recent pciutils; older versions may list the capability without decoding it). | `eq.Gen4` (`eq_16gt_complete`) |
| `Capabilities: [...] Secondary PCI Express` with `LaneErrStat` | Per-lane error status (which lane reported errors). | the lane named in S15 error reasons |
| `Capabilities: [...] Advanced Error Reporting` with `CESta: RxErr- BadTLP- BadDLLP- ...` | Correctable error status bits (Receiver Error, Bad TLP, Bad DLLP, Replay Timer Timeout, ...). | `rx_errors` (shown as `CESta: RxErr` in the model's lspci view) |

How to compare with the model:

1. Read LnkCap and LnkSta on **both** ends of the link.
2. If LnkSta speed is lower than the lower of the two LnkCaps, read
   LnkCtl2 Target Link Speed on the downstream port (S16), then LnkSta2 and
   the 16 GT/s status (S04, S05).
3. If the width is lower than both LnkCaps, check the slot's electrical
   width and bifurcation settings, then the lanes (S06).

## sysfs (Linux)

```sh
d=/sys/bus/pci/devices/0000:03:00.0
cat $d/current_link_speed $d/current_link_width    # for example "8.0 GT/s PCIe" and "16"
cat $d/max_link_speed     $d/max_link_width
cat $d/aer_dev_correctable                          # per-type correctable error counts (AER enabled kernels)
cat $d/aer_dev_fatal $d/aer_dev_nonfatal
```

The AER counter files exist only when the kernel has AER support and the
device has the AER capability; root ports also expose totals such as
`aer_rootport_total_err_cor`. The kernel log often reports reduced
bandwidth at boot with a message similar to "... available PCIe bandwidth,
limited by 8.0 GT/s PCIe x8 link at ...", naming the bridge that limits the
link.

Useful kernel log searches:

```sh
dmesg | grep -iE "aer|pcie bus error|link down|link up|downgrad|bandwidth"
```

## Retraining and Target Link Speed with setpci (test systems only)

`setpci` addresses registers relative to a capability. `CAP_EXP` is the PCI
Express capability; Link Control is at offset `0x10`, Link Status at `0x12`,
Link Control 2 at `0x30`. Run these on the **downstream port** above the
device (here `00:01.0`), as root.

```sh
# read Link Status (current speed in bits 3:0, width in bits 9:4)
setpci -s 00:01.0 CAP_EXP+0x12.w

# set Target Link Speed (LnkCtl2 bits 3:0): 1=2.5, 2=5.0, 3=8.0, 4=16.0 GT/s
setpci -s 00:01.0 CAP_EXP+0x30.w=0x0003:0x000F      # value:mask, only bits 3:0 change

# set Retrain Link (LnkCtl bit 5) to make the port retrain at the new target
setpci -s 00:01.0 CAP_EXP+0x10.w=0x0020:0x0020

# read the result
sudo lspci -s 00:01.0 -vvv | grep -E "LnkCap|LnkSta|LnkCtl2|DLActive|Equalization|Phy16"
```

What to expect, in model terms: the downstream port goes L0 -> Recovery and
back (row T27 in the model), changing speed if the target allows it. Lower
the target to isolate a speed-dependent problem (if errors stop at 8 GT/s,
the channel lacks margin at 16 GT/s); raise it back afterwards. Some
platforms and drivers manage link speed themselves and may change it again.

Secondary Bus Reset (bit 6 of Bridge Control, `BRIDGE_CONTROL` in setpci)
resets everything below a bridge, like scenario S10. It removes devices
from under their drivers; prefer the kernel's own reset interfaces (for
example `/sys/bus/pci/devices/<bdf>/reset` where supported) and treat a
manual Secondary Bus Reset as a last resort on a test system.

## Windows

Windows has no built-in equivalent of `setpci`, and Device Manager does not
show link speed on its main pages. Options, from simplest to most
detailed:

* **Device Manager**: device -> Properties -> Details tab. Recent Windows
  versions list properties such as "PCI current link speed", "PCI current
  link width", "PCI maximum link speed" and "PCI maximum link width"
  (names and availability vary by version). Values may be numeric codes
  rather than GT/s.
* **PowerShell** (`Get-PnpDeviceProperty`), reading the same device
  properties:

  ```powershell
  $dev = Get-PnpDevice -FriendlyName "*NVMe*" | Select-Object -First 1
  Get-PnpDeviceProperty -InstanceId $dev.InstanceId |
      Where-Object KeyName -like "DEVPKEY_PciDevice_*Link*" |
      Format-Table KeyName, Data
  ```

  `Get-PnpDevice` itself only lists devices and their status; the link
  properties come from `Get-PnpDeviceProperty`. Check Microsoft's
  documentation for how the numeric speed codes map to data rates on your
  Windows version.
* **Vendor and third-party tools**: GPU vendors' tools (for example
  `nvidia-smi -q` reports current and maximum PCIe generation and link
  width on NVIDIA GPUs), storage vendors' utilities, hardware information
  tools, and chipset or switch vendors' debug tools, which can also show
  LTSSM state and history on many devices.

## Deeper tools

* **LTSSM state and history**: many root complexes, switches and retimers
  have vendor debug registers or management interfaces (I2C/SMBus, UART,
  BMC) that report the current LTSSM state and sometimes a history buffer.
  This is the closest real-world equivalent of the model's trace.
* **Lane Margining at the Receiver**: a 16 GT/s feature that measures
  per-lane timing and voltage margin in-system. Recent pciutils releases
  include a `pcilmr` utility for it.
* **Bench equipment**: protocol analyzers show the ordered sets each side
  sends (for example TS1 where TS2 was expected, S08); oscilloscopes and
  BERTs measure eyes and margins per lane and direction (S04, S05, S15).

## Mapping model scenarios to real checks

| Scenario | First real-hardware check |
|---|---|
| S02 Mixed capability | LnkCap/LnkCap2 of the endpoint |
| S03 Empty slot | presence, power, PERST#, LnkSta Width x0 |
| S04 / S05 EQ failure | LnkSta2 / 16 GT/s EQ status bits, forced retrain at the higher target |
| S06 Lane failure | LnkSta width vs LnkCap, bifurcation, connector |
| S07 / S15 Flapping | AER correctable counters, LaneErrStat, margining |
| S08 Stuck in Polling | LTSSM state on both ends, protocol analyzer |
| S09 Upstream link down | the highest missing bridge's link |
| S10 Hot Reset | kernel log for resets |
| S11 ASPM L1 | LnkCtl ASPM setting, ASPM policy |
| S12 Lane reversal | board lane map, vendor lane map registers |
| S14 Link down | kernel log, hot-plug and AER/DPC events |
| S16 Target speed cap | LnkCtl2 Target Link Speed, BIOS settings |
