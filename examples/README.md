# Executable local acceptance demonstration

Run `python3 -B examples/local-demo.py` from the package directory. It uses temporary directories, the actual command-line entrypoint in separate subprocesses, and a deliberately faulty Python module.

It verifies that an unapproved check is rejected, an intentional regression baseline fails correctly, the actual root-cause repair passes, the finding can then resolve, and final acceptance requires a review artifact. It then edits the subject to another same-length value while preserving its mtime, verifies that the former COMPLETE result becomes incomplete, and repairs/retests/reviews the final candidate.

The demonstration ends with a real generated report and an explicit success marker only after these assertions. Temporary fixture paths shown in its transcript are removed afterward. This is not a live Claude Code session or a measurement of agent obedience; it tests the executable local workflow. It uses a clearly labeled self-review fixture, not an invented independent reviewer.
