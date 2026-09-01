# J01: Acquisition and Installation Delivery Design

| Status | completed (macOS arm64 / CPython 3.13 Alpha package) |
|---|---|

## Objective

After installing one Assayer deliverable, Skill, MCP, rules, and schemas are available automatically. Internal dependencies never require repository paths, configuration edits, or manual file synchronization. Python and Chromium are explicit external prerequisites and are not installed by this phase.

## Current Implementation

The repository contains a verifiable `plugins/assayer` Plugin:

- `.codex-plugin/plugin.json`: plugin metadata and entrypoint guidance;
- `.mcp.json`: stable in-plugin launcher with no business URL binding;
- `skills/assayer-audit/SKILL.md`: user-journey audit behavior;
- `scripts/launch_assayer_mcp`: fails explicitly when the Python package is absent and never degrades to smoke;
- `runtime/wheels/`: complete offline wheelhouse for the current platform and Python ABI, used to create a private runtime on first use;
- `runtime/bundle-manifest.json`: fixes Plugin/Python/platform versions and SHA-256 for every wheel; validated before startup.

## J01 Gates

The Python distribution installs rules and schemas in relocatable `share/assayer` resources. The release builder downloads Assayer and all internal Python dependencies into an offline wheelhouse, verifies offline installation and 17 tools, and creates one Plugin zip. Alpha builds are platform- and Python-minor-specific; mismatches fail explicitly.

1. Completed: package `rules/`, `schemas/`, and `schemas/protocol/` as distribution data with one resource locator;
2. Completed at build level: launcher creates a private environment from the offline wheelhouse without repository paths;
3. Completed at build level: Plugin/Python versions match and wheel integrity is checked by SHA-256 manifest;
4. Completed at installation level: one Plugin installed and enabled from personal marketplace; cached launcher creates its private runtime offline; fresh-task discovery belongs to J02;
5. Missing external prerequisites produce actionable error codes and never skip silently.

## Delivery Choice

Plugin owns Codex discovery and launcher; the Python distribution owns execution. The release builder combines both and all internal dependencies into a same-version archive. Users neither understand nor connect them manually. J01 passed build and installation gates; automatic discovery outside the repository moves to J02.

## Acceptance Evidence (2026-09-01)

- Fast suite: 195 tests, zero skipped;
- Plugin Creator validation passed for source Plugin and Codex installation cache;
- Release builder produced an approximately 48 MB platform bundle containing fixed Assayer, Playwright, MCP, jsonschema, and transitive dependencies;
- A fresh temporary venv completed `--no-index` offline installation, loaded registry `1.0.0`, and exposed 17 MCP tools;
- `assayer@personal` showed `installed, enabled`, version `0.1.0`;
- First launcher startup produced zero stdout bytes, created its private runtime, and did not contaminate MCP stdio;
- Alpha supports only manifest-declared macOS arm64 / CPython 3.13. Launcher prefers matching `python3.13` instead of an older PATH `python3`; other platforms/minors return `ASSAYER_BUNDLE_INCOMPATIBLE`;
- Legacy user- and repository-level absolute-path MCP wiring was removed so it cannot shadow the Plugin source.
