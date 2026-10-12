/**
 * types/commands.ts
 *
 * AI Chat Module (M09) command/response types, mirroring
 * backend/services/llm/models.py (SDS Section 4, M09).
 */

export type CommandType = "CreateComponent" | "EditProperty" | "UpdateSelection";

export interface Command {
  type: CommandType;
  params: Record<string, unknown>;
}

export type Intent = "create_component" | "edit_property" | "query" | "explain";

export interface AIResponse {
  intent: Intent;
  commands: Command[];
  text: string;
  suggestions: string[];
  /** Day-31: server-issued id of each proposal in `commands` (same order), for POST /api/ai/confirm. */
  proposal_ids: string[];
}

/** Day-31: lifecycle of one AI EditProperty proposal in the chat. */
export type ProposalStatus = "pending" | "applying" | "applied" | "failed" | "dismissed";

export interface EditProposal {
  id: string;
  targetGuid: string;
  targetName: string | null;
  pset: string;
  property: string;
  /** Canonical value the server validated, in millimetres. */
  valueMm: number;
  status: ProposalStatus;
  /** Server text after applying, or the reason it was not applied. */
  outcome?: string;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  timestamp: string;
  /** Day-31: set when this message is an EditProperty proposal awaiting confirmation. */
  proposal?: EditProposal;
}
