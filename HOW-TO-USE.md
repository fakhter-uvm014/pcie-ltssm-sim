# How to use the PCIe LTSSM Simulator

A step-by-step guide for first-time users on **Windows** (PowerShell) or **Linux/macOS**.
You need no PCIe hardware: everything runs in Python, and the viewer runs in any web browser.

By the end you will have:

1. installed and validated the simulator (about 10 minutes);
2. replayed link training in the browser viewer;
3. walked through the scenarios in a learning order; and
4. practised debugging with mystery mode.

---

## 1. One-time setup

### Windows

1. Install **Python 3.10 or newer** from <https://www.python.org/downloads/>.
   On the first installer screen, tick **"Add python.exe to PATH"**, then click *Install Now*.
2. Open **PowerShell**: press the Start key, type `powershell`, press Enter.
3. Check the install:
   ```powershell
   py --version
   ```
   You should see `Python 3.10` or newer.

### Linux / macOS

Most systems already have Python 3. Check it with `python3 --version`. On Debian or Ubuntu, if `venv` is
missing, run `sudo apt install python3-venv`.

---

## 2. Get the files and install

Unzip `pcie-ltssm-sim.zip` somewhere simple, for example `C:\Users\<you>\projects\pcie-ltssm-sim`.

**Open a terminal in that folder.** On Windows: open the folder in File Explorer, click the address
bar, type `powershell` and press Enter.

| Step | Windows (PowerShell) | Linux / macOS |
|---|---|---|
| Create a private Python environment | `py -m venv .venv` | `python3 -m venv .venv` |
| Turn it on | `.\.venv\Scripts\Activate.ps1` | `source .venv/bin/activate` |
| Install the simulator | `python -m pip install -e ".[dev]"` | `python -m pip install -e ".[dev]"` |

When the environment is on, your prompt starts with `(.venv)`.

> **PowerShell says "running scripts is disabled"?** Run this once, answer `Y`, then activate again:
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

---

## 3. Validate it (10-minute checklist)

Run these from the repository folder with `(.venv)` active. The commands are the same on Windows and Linux.

| # | Command | What you should see |
|---|---|---|
| 1 | `python -m ltssm list` | A table of 16 scenarios, S01 to S16 |
| 2 | `python -m ltssm run S01` | Links A, B, C in `L0` at Gen4; link D (empty slot) in `Detect.Quiet`; last lines `Result: PASS` |
| 3 | `python -m ltssm run S04` | Link B ends at **Gen2 x16** after two failed Gen3 equalization attempts; `Result: PASS` |
| 4 | `python -m ltssm run-all` | One line per scenario, all `PASS`, ending `All 16 scenarios match their expected outcome.` |
| 5 | `python -m pytest -q` | Ends with `... passed` and no failures (takes about 20 seconds) |
| 6 | `python scripts/view.py` | Your browser opens the viewer at `http://localhost:8000/` |

If all six steps look like this, the project works on your machine.

---

## 4. Use the viewer

```
python scripts/view.py
```

This starts a small web server **on your own computer only** and opens your browser.
Leave the terminal open while you use the viewer, and press `Ctrl+C` there to stop it.
(Double-clicking `docs/index.html` does not work: browsers block a page opened from disk from
loading the trace files.)

What is on the page, top to bottom:

| Panel | What to do with it |
|---|---|
| **Scenario** | Pick a scenario. The description and the expected outcome appear underneath, with a PASS/FAIL pill. |
| **Topology** | Root port, switch and endpoints. Each link is coloured by state and labelled with speed and width at the playhead time. |
| **Replay** | *Play*, or step with *Next*/*Prev*; the speed slider changes the playback rate. The table lists every link's state at the playhead. |
| **Timeline** | One lane per port, coloured by LTSSM state (Detect grey, Polling amber, Configuration purple, L0 green, Recovery blue, Equalization teal). Hover a bar for substate, reason, speed and width. Zoom with `+`/`-`, or click *Fit*. |
| **Event log** | Every state change, with a plain-English reason. Filter by port, state or text. |
| **Summary** | The final Link Status per port, the counters, the enumeration tree, and each pass/fail check. |
| **Learning notes** | What to look for, likely real-hardware causes and how to debug. Tick **Mystery mode** to hide the cause until you choose to reveal it. |

---

## 5. Guided tour: what to run, what to notice

Work through the scenarios in this order. For each one, run it in the terminal, then open it in the viewer.

| Order | Scenario | Run | What to notice |
|---|---|---|---|
| 1 | **S01** clean bring-up | `python -m ltssm run S01 --trace` | The normal sequence: Detect -> Polling -> Configuration -> L0 at Gen1, then Recovery with equalization to Gen3 and then Gen4. Learn this baseline first. |
| 2 | **S02** Gen2-only endpoint | `python -m ltssm run S02` | A link runs only as fast as the slower partner. Gen2 needs no equalization. |
| 3 | **S06** dead lanes | `python -m ltssm lspci S06` | Link A trains at x8 instead of x16. Compare `LnkCap` (what the port can do) with `LnkSta` (what it negotiated). |
| 4 | **S12** lane reversal | `python -m ltssm run S12 --trace` | Full width despite reversed wiring; the lane numbers flip in Configuration. |
| 5 | **S04** Gen3 EQ failure | `python -m ltssm run S04 --trace` | Equalization fails in Phase 2 twice; the link falls back to Gen2, and the trace says why. |
| 6 | **S05** Gen4 EQ failure | `python -m ltssm lspci S05` | Gen3 equalization passes and Gen4 fails, so the link settles at Gen3. Look at the EQ status lines. |
| 7 | **S03** empty slot | `python -m ltssm run S03` | Link D loops Detect.Quiet <-> Detect.Active; `detect_cycles` keeps climbing. |
| 8 | **S09** upstream link down | `python -m ltssm enumerate S09` | If link A never trains, nothing behind the switch exists to software. |
| 9 | **S07** flapping | `python -m ltssm run S07` | Repeated L0 <-> Recovery. Read `flap_count` and `recovery_count`. |
| 10 | **S08**, **S10**, **S11**, **S13-S16** | `python -m ltssm explain S08` and so on | Polling timeout, Hot Reset, L1 power state, speed-step policy, link-down escalation, recovery storms and a software speed cap. |

Useful extras:

- `python -m ltssm explain S07` prints the learning notes: what to look for, likely root causes and how to debug.
- `python -m ltssm table` prints the LTSSM transition table, the rules the simulator follows.
- The guides in `docs/guide/`: start with `how-it-works.md`, then `debug-guide.md`.

---

## 6. Practise debugging (mystery mode)

| Command | What it does |
|---|---|
| `python -m ltssm run S06 --hidden` | Shows only the symptoms (link status, counters), as you would see them on a bench |
| `python -m ltssm run S06 --reveal` | Shows the injected fault and the full explanation |
| `python -m ltssm quiz` | Picks a random scenario, hides the cause and asks you to choose from a numbered list |
| `python -m ltssm quiz --seed 3 --answer 2` | The same quiz without typing (repeatable) |

Method to practise:

1. Read the symptom.
2. Compare `LnkSta` with `LnkCap`.
3. Look at the counters.
4. Form one hypothesis.
5. Check it in the trace.
6. Only then reveal.

`docs/guide/debug-guide.md` has the full symptom -> evidence -> cause table.

---

## 7. Command cheat sheet

| Command | Purpose |
|---|---|
| `python -m ltssm list` | All scenarios and expected outcomes |
| `python -m ltssm explain <ID>` | Learning notes for one scenario |
| `python -m ltssm run <ID>` | Run one scenario: summary + PASS/FAIL + "What this means" |
| `python -m ltssm run <ID> --trace` | Also print every state change with its reason |
| `python -m ltssm run <ID> --hidden` / `--reveal` | Mystery mode |
| `python -m ltssm lspci <ID>` | `lspci -vvv`-style view of each port after training |
| `python -m ltssm enumerate <ID>` | `lspci -t`-style bus tree with bus numbers |
| `python -m ltssm table` | The LTSSM transition table |
| `python -m ltssm quiz` | Random hidden-cause quiz |
| `python -m ltssm run-all` | Run everything and refresh the viewer's traces |
| `python scripts/view.py` | Open the viewer |
| `python -m pytest -q` | Run the test suite |

---

## 8. Troubleshooting

| Problem | Fix |
|---|---|
| `py` or `python` is not recognised | Reinstall Python with "Add python.exe to PATH" ticked, then open a **new** PowerShell window. |
| Typing `python` opens the Microsoft Store | Use `py` to create the environment. Inside the activated `.venv`, `python` works. Or turn off *App execution aliases* for Python in Windows Settings. |
| "running scripts is disabled on this system" | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, then activate again. |
| `No module named yaml` or `No module named ltssm` | The environment is not active (no `(.venv)` in the prompt). Activate it and try again. |
| The viewer says it cannot load traces | Start it with `python scripts/view.py` (not by double-clicking the HTML file). |
| Port 8000 already in use | `python scripts/view.py --port 8080` (the script also picks a free port automatically). |
| You edited a scenario and the viewer shows old data | Run `python -m ltssm run-all` to refresh `docs/traces/`. |

## 9. Coming back later

Open PowerShell in the folder, run `.\.venv\Scripts\Activate.ps1` (Linux: `source .venv/bin/activate`),
and carry on. There is no need to install again.

---

Author: Farzana Akhter
