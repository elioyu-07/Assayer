# Design Confirmation records

Every platform, Provider, Agent, SDK, contract, release, and plugin change
requires one versioned JSON record in this directory before implementation.

Validate a record with:

```text
python3 scripts/check_design_confirmation.py design/changes/<change-id>.json
```

Do not edit an approved record after implementation begins. Create a new
change ID when scope, ownership, capability, compatibility, or acceptance
changes.
