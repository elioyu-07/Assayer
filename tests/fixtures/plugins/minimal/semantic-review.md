# Minimal Fixture Semantic Review

This fixture exists to exercise the external-plugin installation and
deterministic-fixture gates. It reads a JSON configuration file and reports a
deterministic `issue_found` decision when a required key is absent.
