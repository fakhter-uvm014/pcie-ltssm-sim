# Glossary

Terms as they are used in this project. Where the model simplifies a real
concept, the entry says so.

| Term | Meaning |
|---|---|
| **AER** | Advanced Error Reporting. A PCIe capability that records correctable and uncorrectable errors. The model counts receiver errors in L0 (`rx_errors`) as a stand-in for AER Receiver Error. |
| **ASPM** | Active State Power Management: hardware-controlled low-power link states (L0s, L1) entered when the link is idle. |
| **Bifurcation** | Splitting one wide slot into several narrower links (for example x16 into x8x8), set by BIOS or straps. A wrong setting shows up as a narrower link. |
| **Burst** | Model term: a batch of identical ordered sets sent on every lane in one engine event (32 per lane, larger when nothing changes). |
| **DL_Link_Active (DLActive)** | Link Status bit: the Data Link Layer is up. In the model it is a timed flag set 5 us after L0 and cleared when the link goes to Detect, Hot Reset, Disabled or L2. |
| **DSP / USP** | Downstream-facing port (root port, switch downstream port) and upstream-facing port (switch upstream port, endpoint). The DSP leads Configuration; in the model it also initiates every speed change (on real links either port may). |
| **EC field** | Equalization Control field in TS1 at 8 GT/s and above; tells the partner which equalization phase the sender is in. |
| **EIOS / EIEOS** | Electrical Idle Ordered Set (the transmitter is about to go idle) and Electrical Idle Exit Ordered Set. |
| **Electrical Idle (EI)** | A transmitter driving no data. Leaving it ("EI exit") is how a port notices its partner is active. |
| **Enumeration** | Software's walk of configuration space that finds devices and assigns bus numbers. |
| **Equalization (EQ)** | Tuning transmitter (and receiver) settings at 8 GT/s and above in four phases (0-3). The model uses symbolic presets and a symbolic figure of merit. |
| **Figure of merit (FOM)** | Model term: a 0-100 number standing in for eye quality when a preset is tried. A preset passes at 60. |
| **Flap** | A Recovery pass caused by an error, counted in `flap_count`. |
| **FTS** | Fast Training Sequence, used to leave L0s. |
| **Gen1-Gen4** | 2.5, 5.0, 8.0 and 16.0 GT/s. Gen1/Gen2 use 8b/10b encoding, Gen3/Gen4 use 128b/130b. |
| **Hot Reset** | An in-band reset signaled with TS1 ordered sets; everything below the port is reset and retrains. |
| **L0, L0s, L1, L2** | Link up; transmit-side low-power standby; deeper low-power state (exited through Recovery); main power off. |
| **Lane reversal** | Lanes wired in reverse order (lane 0 to lane N-1); detected and handled during Configuration. |
| **Link number / lane number** | Numbers exchanged in TS1/TS2 during Configuration; PAD means "not assigned yet". |
| **LnkCap / LnkCtl / LnkSta (and ...2)** | Link Capabilities, Control and Status registers in the PCI Express capability, as printed by `lspci -vvv`. |
| **LTSSM** | Link Training and Status State Machine: the per-port state machine this project models. |
| **Mystery mode** | Project feature: show only symptoms (`--hidden`, viewer toggle) and guess the cause before revealing it. |
| **Ordered set** | A fixed-format block of symbols used for link management (TS1, TS2, EIOS, SKP, FTS...). Symbolic objects in the model. |
| **PERST#** | Fundamental reset signal for a slot or device. The model starts every port as PERST# is released. |
| **Preset (P0-P10)** | Named transmitter equalization settings. In the model they are names only. |
| **Rate ceiling** | Model term: the highest rate the DSP will still try after equalization failures. |
| **Receiver detection** | An electrical test in Detect.Active: does the other end present receiver termination on a lane? |
| **Recovery** | The LTSSM state used to re-acquire lock, change speed, equalize, and to signal Hot Reset, Disable or Loopback. |
| **Requester / responder** | Model terms for equalization: the requester judges the eye and asks for presets (USP in Phase 2, DSP in Phase 3); the responder applies them. |
| **Retrain Link** | A Link Control bit on downstream ports that forces the link through Recovery. |
| **Retimer / redriver** | Signal conditioning devices on long channels; a frequent suspect in equalization and flapping problems. Not modeled. |
| **SRIS / SSC** | Separate Reference clock with Independent Spread spectrum clocking / Spread Spectrum Clocking. Clocking mismatches are a classic cause of link errors. Not modeled. |
| **Target Link Speed** | A Link Control 2 field that caps the link speed the downstream port will advertise. |
| **TS1 / TS2** | Training Sequence ordered sets. TS1 proposes, TS2 confirms. |
| **Width (x1-x16)** | Number of lanes in the link, negotiated in Configuration. |
