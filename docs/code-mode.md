# Code mode

Use `run_code` to chain tools and filter intermediate results in one Python
program. It is available alongside direct tools without additional configuration.
For example, ask Cade to use `run_code` to read several files and return only
their headings.

## Write a program

The `code` argument is the body of an async Python function. Top-level `await`
and `return` work. Call tools with the same arguments as direct calls:

```python
result = await tools.read({"path": "README.md"})
return [line for line in result["content"].splitlines() if line.startswith("#")]
```

Use `await tools.name({...})` or `await tools["name"]({...})`. Successful calls
return `{content, metadata}`. Metadata preserves the tool's existing structured
data, including MCP `mcp_result`. Failed or denied calls raise `RuntimeError`,
which the program can catch.

Only printed text and the JSON return value enter model history. Other tool
results stay inside the program. Each execution starts a fresh Python process;
variables do not persist between calls. `asyncio` is available, but tool calls
execute serially, including those submitted with `asyncio.gather`. Scripts
cannot call `run_code` recursively.

## Results and limits

The optional `timeout` argument sets the total deadline in seconds, including
nested tool execution and approval waits. It must be a positive finite number,
with no fixed maximum. Omit it to use the default 120 seconds. For example:

```json
{"code": "await asyncio.sleep(180)\nreturn 'done'", "timeout": 300}
```

Nested tools inherit the selected timeout as their default; the whole program
still shares one total deadline. You can cancel the program before it expires.
Captured text and the return value share a 50 KiB budget; control messages have
a 1 MiB limit. Exceeding either size limit returns an error.

A script error returns the captured output and error. Failure does not undo
completed actions or automatically retry the program. Cancellation and timeouts
terminate the Python process and wait for started nested calls to settle.

Nested calls have `tool_execution` audit records outside model history. Use
`history` to retrieve those records. Writes through `tools.write` and
`tools.edit` retain tracked file recovery.

See [Tools and security](tools-security.md#python-execution-permissions) for
execution permissions and direct Python I/O, and
[decision 0019](decisions/0019-python-tool-orchestration.md) for the design.
