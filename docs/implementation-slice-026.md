# Vertical Slice 026: General Interaction and Binding Evidence (C03)

C03 adds opaque control/list references, typed value classes, synthetic input and option selection, query/reset actions, and before/after interaction Evidence. Host keeps original values only in Session memory and restores them through internal handles. Lists persist irreversible summaries, visibility, item counts, and busy state.

Query/reset and selection pass pre-send safety gates; writes, cross-origin, active transports, and unknown requests remain fail-closed. Tests verify list differences, restored values, binding Evidence, and rejection of raw values, unknown refs, mismatched actions, and disallowed value classes.
