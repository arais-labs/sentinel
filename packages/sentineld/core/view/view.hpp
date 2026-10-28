// sentineld view (DESIGN.md §6): a workspace's desktop in a native macOS window.
#pragma once

namespace sentineld {

// `sentineld view …`: argv as main got it. Runs the window until it closes; the exit status.
int view(int argc, char** argv);

}  // namespace sentineld
