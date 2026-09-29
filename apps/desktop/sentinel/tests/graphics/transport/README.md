# GPU transport regression tests

Run on macOS or Linux with a C11 compiler and Python 3:

```sh
python3 -m unittest discover -s tests/graphics/transport -p 'test_*.py' -v
SENTINEL_TRANSPORT_SANITIZERS=1 python3 -m unittest discover -s tests/graphics/transport -p 'test_*.py' -v
```

The socket tests move four independently verified 32 MiB streams over one
physical connection. Both command consumers remain stopped until both resource
streams finish; command buffering must remain bounded. They also cover malformed
headers/credits, receive-window overruns, truncation, fragmented payload delivery,
disconnects with buffered bytes, cancellation, and caller descriptor ownership.
All blocking test operations have bounded deadlines; there are no sleep loops.

A deterministic scheduler test compiles the real transport implementation into
its test translation unit. It replenishes credit before every emitted frame and
requires both ready DATA streams to progress after at most two CREDIT frames.
With no ready DATA, credits must resume immediately. This checks sustained-traffic
fairness independently of OS scheduling and finite transfer durations.

The production pump uses no heap allocations and enforces its fixed memory bound
with a compile-time assertion: two 64 KiB receive rings, one 16 KiB outbound DATA
frame, and at most 256 bytes of framing/state overhead. Socket kernel buffers are
additional OS-managed bounded storage. Passing these tests proves the transport
contract, not graphics correctness or physical display frame rate.
