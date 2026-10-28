// sentineld ctl (DESIGN.md §4): sends one request to a running sentineld and prints its reply.
#pragma once

namespace sentineld {

// `sentineld ctl …`: argv as main got it. The exit status.
int ctl(int argc, char** argv);

}  // namespace sentineld
