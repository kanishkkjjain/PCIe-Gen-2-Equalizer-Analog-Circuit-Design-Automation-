"""RL-based automated analog equalizer (PCIe CTLE) design framework.

Modules
-------
pdk      : locate SKY130 model libraries and build corner include lines.
netlist  : parametric SPICE netlist builder for the CTLE (+ later DFE).
spice    : ngspice batch runner and result parsers (AC / TRAN / NOISE / OP).
specs    : target specification container and a differentiable-ish scorer.
env      : Gymnasium environment exposing the sizing problem to an RL agent.
agents   : sample-efficient RL agents (Cross-Entropy Method policy search).
design   : top-level orchestrator -> specs in, sized schematic + specs out.
"""

__version__ = "0.1.0"
