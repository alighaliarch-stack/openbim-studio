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
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  timestamp: string;
}
