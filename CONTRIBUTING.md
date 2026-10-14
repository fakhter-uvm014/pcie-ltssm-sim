# Contributing

Thank you for helping improve the PCIe LTSSM Simulator. The project is a
learning tool, so clarity matters more than features: every change should
make it easier to understand what a link is doing and why.

## Set up

```sh
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
pytest
```

## Ground rules

- Standard library only in `ltssm/` (PyYAML is used to read scenario and
  topology files).
- Deterministic: all randomness goes through the link's seeded
  `random.Random`; the same seed must give a byte-identical trace.
- Console output is plain ASCII; colors only with `--color`.
- Use `pathlib`, open files with `encoding="utf-8"`, and keep everything
  working on Windows and Linux.
- Every trace event needs a human-readable reason.
- Do not put dates in files, and describe the model as a behavioral model
  for learning and demonstration (not spec-accurate, not cycle-accurate).
  `tests/test_repo.py` checks the wording rules.

## Changing the LTSSM

1. Add or edit a row in `ltssm/transitions.py`: source substate, role,
   event name, guard (in words), destination, optional action, and a one-line
   rule. Keep rows in priority order and keep ids in order.
2. Implement `Port.cond_<event>()` (read-only; return `None` or a reason
   string) and, if needed, `Port.act_<action>()` in `ltssm/port.py`.
3. If a built-in scenario does not use the row, mark it `optional=True` and
   add an exercise for it in `tests/test_transitions.py`.
4. Regenerate the table document:
   `python -m ltssm table --markdown > docs/guide/transition-table.md`.

## Adding a scenario

1. Copy a file in `scenarios/` to `S<NN>_<short_name>.yaml` (the id must
   match the file name prefix).
2. Fill in `description`, `faults` / `commands` / `config`, the expected
   `summary` and machine-checked `checks`, and the learning notes:
   `symptom`, `what_to_look_for`, `what_this_means`,
   `likely_root_causes`, `how_to_debug`, `mystery_hints`, `mystery_cause`.
   Keep the text ASCII (it is printed to the console) and hedge claims about
   real hardware.
3. Add a test to `tests/test_scenarios.py` named
   `test_S<NN>_<what_it_shows>` with a Test ID / Purpose / Stimulus /
   Expected result docstring.
4. Run `python -m ltssm run-all` to refresh `docs/traces/` and the index,
   `python scripts/gen_scenario_catalog.py` to rebuild
   `docs/guide/scenario-catalog.md`, update the README table, and run
   `pytest`.

## Viewer

`docs/` is plain HTML, CSS and JavaScript with no build step. Keep paths
relative (it must work from `python -m http.server --directory docs` and
from GitHub Pages), keep it usable at 375 px wide, and check light and dark
color schemes.

## Pull requests

Describe what a learner will see differently (a new reason in the trace,
a new scenario, a clearer view). CI must pass on Ubuntu and Windows. The
repository lives at https://github.com/fakhter-uvm014/pcie-ltssm-sim.
