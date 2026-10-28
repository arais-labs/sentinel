// sentineld --update-session ROOT: the update coordinator on a remote Mac (DESIGN.md §23).
#pragma once

namespace sentineld {

// Serves the backend's update requests on stdin/stdout until EOF; returns the exit status.
int updateSession(const char* root);

}  // namespace sentineld
