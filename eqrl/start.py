"""Which starting-point derivation the search begins from.

Three derivations exist and all three are real:

``physics``
    ``physics.analytic_start_u`` -- the shipped one, and the one the saved
    policy was TRAINED against. No SPICE, ~1.2 s per spec.
``physfit``
    ``start_physfit.derive`` -- the same closed-form derivation with every
    device estimate taken from ``PhysFit``. ~0.2 s per spec, and measured at
    the same 8/10 coverage with 7 of 10 solved at rank 1
    (``results/solve_rate_physfit.json``).
``systematic``
    ``start_systematic.derive`` -- the same derivation again, but with every
    device value measured by ngspice. ~19.8 s per spec and the same 8/10. Kept
    because the committed measurements used it; too slow for a server.

WHY A MODULE AND NOT A MONKEYPATCH. ``start_physfit.patch()`` rebinds module
globals, and one of its three targets is reached through
``sys.modules.get("deploy_policy")`` -- a module the MCP server imports *inside*
``design_equalizer``, long after any import-time patch has run. The patch would
therefore land on the policy path and miss the CEM path, leaving the two
searches starting from DIFFERENT designs. ``deploy_policy.start_for`` exists
specifically to stop that:

    Fairness between methods: CEM and the policy must begin from the SAME
    design, or the comparison measures the starting point rather than the
    search.

So the seam is resolved per CALL rather than per import. There is no window in
which two callers can disagree about which derivation is in force.

The default is ``physics``, so training, data collection and every committed
measurement stay bit-identical unless something opts in explicitly.
"""
from __future__ import annotations

#: None means "the default", resolved lazily so importing this module costs
#: nothing and cannot drag `physics` in ahead of its own dependencies.
_IMPL = None
_NAME = "physics"

IMPLEMENTATIONS = ("physics", "physfit", "systematic")


def _physics_impl():
    from .physics import analytic_start_u
    return analytic_start_u


def _physfit_impl():
    from .start_physfit import start_u
    return start_u


def _systematic_impl():
    from .start_systematic import start_u
    return start_u


_FACTORIES = {"physics": _physics_impl, "physfit": _physfit_impl,
              "systematic": _systematic_impl}


def corner_from(corner_fields=None, corners_cf=None):
    """The one PVT corner a derivation aims at, from either argument shape.

    Callers pass a single `corner_fields` dict or a list `corners_cf`; the
    derivations want one corner, so the first is taken. Anything unrecognised
    falls back to typical, which is what they aimed at anyway. Shared, because
    both `start_physfit` and `start_systematic` need exactly this and they must
    not disagree about it.
    """
    from . import pdk
    cf = corner_fields or (corners_cf[0] if corners_cf else None)
    if not cf:
        return pdk.typical_corner()
    for c in pdk.pvt_grid("spec"):
        if (c.process == cf.get("process")
                and abs(c.vdd - cf.get("vdd", c.vdd)) < 1e-6):
            return c
    return pdk.typical_corner()


def use(name: str) -> str:
    """Select a derivation by name. Returns the name now in force.

    Raises on an unknown name rather than falling back: a silent fallback here
    means a run reports one derivation and uses another, which is the single
    failure this module exists to prevent.
    """
    global _IMPL, _NAME
    if name not in _FACTORIES:
        raise ValueError(
            f"unknown starting point {name!r}; expected one of "
            f"{', '.join(IMPLEMENTATIONS)}")
    _IMPL = _FACTORIES[name]()
    _NAME = name
    return _NAME


def current() -> str:
    """The name of the derivation in force."""
    return _NAME


def reset() -> str:
    """Back to the default. For tests, so one cannot leak into the next."""
    global _IMPL, _NAME
    _IMPL, _NAME = None, "physics"
    return _NAME


def analytic_start_u(spec, fit, **kw) -> list:
    """The seam every caller goes through. Signature of the physics version."""
    impl = _IMPL if _IMPL is not None else _FACTORIES[_NAME]()
    return impl(spec, fit, **kw)
