# Postmortems

A postmortem records an incident that escaped the intended design. It is the
only place for chronology and war stories; current behavior belongs in the
subsystem reference.

## Records

- [0001: Compaction cut pointed at a shadowed user entry](0001-compaction-cut-misalignment.md)
- [0002: One huge tool result exhausted the request budget](0002-single-huge-tool-result.md)

## Template

Copy [TEMPLATE.md](TEMPLATE.md). Keep the timeline factual, name the root cause,
show the fix, and record the test that would have caught it. Do not turn the
postmortem into a tutorial; link the current behavior page instead.

Postmortems are excluded from the published guide manifest but remain valid
links and are reviewed with the fix.
