"""PCIe LTSSM behavioral simulator (Gen1 to Gen4).

Author: Farzana Akhter

A behavioral model for learning and demonstration: it is not spec-accurate,
not cycle-accurate, and not derived from the PCI-SIG specification text.

Start reading at ``ltssm/transitions.py`` (the transition table), then
``ltssm/port.py`` (how one port evaluates the table).

Quick use::

    from ltssm.scenario import find_scenario, run_scenario
    result = run_scenario(find_scenario("S04"))
    print(result.passed, result.trace["links"]["B"]["speed"])
"""

__version__ = "1.0.0"
__all__ = ["__version__"]
