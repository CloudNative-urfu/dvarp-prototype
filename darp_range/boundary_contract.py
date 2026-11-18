from types import MappingProxyType

# Published wire identifiers for frozen executor boundaries (issue #60).
FINALIZING_OPS = ("create_refund", "refuse", "assign_manual_review")
BOUNDARY_FINGERPRINTS = MappingProxyType({
    "executor-boundary-1": "8e46eeb0bbeb37c007f60ecb01f733fa16281d130c9fabf40b89ae3b4515c8bd",
    "executor-boundary-2": "b5118ab3b14ae6dc6b2972ee64c4a40a2127b35d649249330f16f59b04d65801",
    "executor-boundary-2r": "d3d6be7814d064e84f279234e6f5e833d4609896ec5f65b6703c8f64e4a68a9a",
})
BOUNDARY_FINGERPRINT = BOUNDARY_FINGERPRINTS["executor-boundary-1"]
