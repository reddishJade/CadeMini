# 0015: Define consistent tool behavior

Status: accepted

## Context

Cade is a teaching project. Tool behavior should be explicit, predictable,
and covered by examples that exercise actual user outcomes.

## Decision

Workspace tools use stable parameters, defaults, matching rules, truncation
limits, result text, and cancellation behavior. Editing supports disjoint
replacements against one original file. Web tools search through Exa and
retrieve textual content.

Cade applies its permission policy, workspace snapshots, session log, and worker
confinement. Searches exclude `.cade` on every platform. Shell calls support
`workdir` and `additional_write_paths` permission extensions. Native
tools and output files use Cade storage names; image processing uses Pillow.
Search uses the MCP SDK without provider selection or automatic fallback.

Image attachments live in `tool_result.artifact.tool_metadata.images` and survive
projection, replay, and request auditing. This extends the current session
contract without introducing a second history format.

## Consequences

`find` replaces `glob`. `edit` accepts an array of `{oldText, newText}` entries
matched against the original content. Shell timeout values are in seconds and
have no default. `read` returns raw text or an image; `ls` lists directories.

`tests/test_file_tools.py`, `tests/test_questions.py`, and `tests/test_web_tools.py`
check editing, image transport, user questions, and web content outcomes.
HTML conversion uses Markdownify and Beautiful Soup; diff previews use the
Python standard library. Image decoding and resizing use Pillow.
