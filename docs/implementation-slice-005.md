# Vertical Slice 005: Case Recovery and Recovery Barrier

`RecoveryAdapter` returns structured checks; Host never accepts Agent self-reported recovery. `restore_case` performs targeted inverse recovery and refresh/replays safe entrypoints unless every required check is `match`. `restored` alone permits continuation; unknown pending/write state fails the Scan. Attempts, checks, and reasons persist in ReverseCase, and recovery Operations are idempotent. Host restart converges orphan running action/recovery Operations to `result_unknown`, invalidates Cases, and fails the Scan. Successful recovery returns the object to `eligible` and Case to `completed`.

The default recovery adapter is unavailable and fail-closed; deterministic adapters are test-only and do not represent real-browser recovery.
