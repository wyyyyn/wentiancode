"""v0.4 · Agent Loop package (from task T47).

asyncio multi-turn Agent loop: event contracts (events), stream bridging, turn execution, loop scheduling, etc.
Modules are progressively placed in this package.

This package does not re-export anything — upper layers must import directly from concrete submodules (e.g. ``wentian.agent.events``)
to avoid conflicts when parallel tasks modify ``__init__`` concurrently.
"""
