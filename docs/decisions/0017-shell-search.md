# 0017: Use shell commands for file discovery and search

Status: accepted

## Context

Standalone `ls`, `find`, and `grep` overlap with shell commands and add tool
schemas, dependencies, and separate argument handling.

## Decision

Remove these three tools and their implementations. Use `bash` or `powershell`
for directory listings, file discovery, and content search. Keep shell permission
classification, sandboxing, and output limits unchanged. Historical session
records remain readable by the TUI.

This supersedes the standalone search-tool surface in
[decision 0015](0015-tool-behavior-parity.md), not its file-editing contract.

## Consequences

fd and ripgrep are optional shell utilities, not application dependencies.
The [registry](../../src/cade/coding/tools/registry.py) exposes the remaining
workspace tools; [tests](../../tests/test_tools.py) verify the catalog and reject
removed tool names while retaining shell commands.
