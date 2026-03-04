export const DEFAULT_AGENT_NAME = 'Sentinel';
export const DEFAULT_AGENT_ROLE = 'You are a proactive operator assistant for the user.';
export const DEFAULT_USER_PROFILE_HINT =
  'When appropriate, ask the user for context about their goals, preferences, constraints, and environment to fill this memory.';

export interface ResolvedAgentIdentity {
  rawName: string;
  rawRole: string;
  rawPersonality: string;
  finalName: string;
  finalRole: string;
}

export interface ResolvedUserProfile {
  rawName: string;
  rawContext: string;
}

export function resolveAgentIdentity(
  agentName: string,
  agentRole: string,
  agentPersonality: string,
): ResolvedAgentIdentity {
  const rawName = agentName.trim();
  const rawRole = agentRole.trim();
  return {
    rawName,
    rawRole,
    rawPersonality: agentPersonality.trim(),
    finalName: rawName || DEFAULT_AGENT_NAME,
    finalRole: rawRole || DEFAULT_AGENT_ROLE,
  };
}

export function resolveUserProfile(userName: string, userContext: string): ResolvedUserProfile {
  return {
    rawName: userName.trim(),
    rawContext: userContext.trim(),
  };
}

export function buildAgentIdentityMemoryContent(identity: ResolvedAgentIdentity): string {
  const parts = [`You are ${identity.finalName}.`, identity.finalRole];
  if (identity.rawPersonality) parts.push(`Personality: ${identity.rawPersonality}`);
  return parts.join('\n\n');
}

export function buildUserProfileMemoryContent(profile: ResolvedUserProfile): string {
  const parts = [
    profile.rawName ? `The user's name is ${profile.rawName}.` : "The user's name is not known yet.",
    profile.rawContext || "The user's detailed profile is not known yet.",
    DEFAULT_USER_PROFILE_HINT,
  ];
  return parts.join('\n\n');
}
