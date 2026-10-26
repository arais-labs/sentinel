// sentineld's help texts (core/help/*.txt), built into the binary so they always match it: for
// `sentineld help` and the `help` op.
#pragma once

namespace sentineld {
// "" (the overview) or "distributions"; nullptr for an unknown topic.
const char* helpText(const char* topic);
}  // namespace sentineld
