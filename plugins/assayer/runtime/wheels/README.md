# Release wheelhouse

Release builds place the Assayer wheel and every Python dependency required by
the `browser` and `mcp` extras in this directory. The launcher installs only
from this directory with `--no-index`; it never downloads dependencies at use
time. This source placeholder is not a releasable bundle by itself.
