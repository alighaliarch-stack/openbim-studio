/**
 * components/aichat/AIChatPanel.tsx
 *
 * M09 AI Chat Module — frontend panel (SDS Section 4, M09 / SRS Flow 2).
 *
 * Sends free-text prompts to POST /api/ai/prompt, displays the streamed-
 * in (MVP: single-shot) response, and — per the api/ai.py docstring's
 * design note — inspects returned `commands` to issue MVP-appropriate
 * follow-up calls (e.g. a CreateComponent command triggers a SCAD render
 * + model refetch), since M07 Command Interpreter does not exist yet to
 * own that orchestration.
 */

import { useState } from "react";

import { ChatMessage } from "@/components/aichat/ChatMessage";
import { apiClient, useModelContext } from "@/context/ModelContext";
import { useSelection } from "@/hooks/useSelection";
import type { ChatMessage as ChatMessageType, Command } from "@/types/commands";

export function AIChatPanel() {
  const [messages, setMessages] = useState<ChatMessageType[]>([]);
  const [draft, setDraft] = useState("");
  const [isSending, setIsSending] = useState(false);

  const { primaryGuid } = useSelection();
  const { refetch } = useModelContext();

  async function handleSend() {
    const prompt = draft.trim();
    if (!prompt || isSending) return;

    appendMessage({ role: "user", text: prompt });
    setDraft("");
    setIsSending(true);

    try {
      const response = await apiClient.sendAiPrompt({ prompt, selected_guid: primaryGuid });
      appendMessage({ role: "assistant", text: response.text });
      await executeCommands(response.commands);
    } catch (err) {
      // SRS Alternative flow: "LLM API fails -> chat shows error, suggests retry".
      const message = err instanceof Error ? err.message : "Unknown error";
      appendMessage({ role: "assistant", text: `Something went wrong: ${message}. Please retry.` });
    } finally {
      setIsSending(false);
    }
  }

  async function executeCommands(commands: Command[]) {
    for (const command of commands) {
      if (command.type === "CreateComponent") {
        // TODO: This is a minimal MVP orchestration. Once M07 Command
        // Interpreter exists, route through it instead so the render is
        // undoable and the resulting entity is added to M10 atomically.
        const componentType = String(command.params.component_type ?? "");
        await apiClient.renderScad({ component_type: componentType, parameters: command.params });
        await refetch();
      }
      // EditProperty / UpdateSelection commands: TODO(Phase 2) — wire once
      // the AI is expected to directly manipulate existing entities rather
      // than only creating new components.
    }
  }

  function appendMessage(partial: Omit<ChatMessageType, "id" | "timestamp">) {
    setMessages((prev) => [
      ...prev,
      { ...partial, id: crypto.randomUUID(), timestamp: new Date().toISOString() },
    ]);
  }

  return (
    <div className="ai-chat-panel">
      <div className="ai-chat-panel__messages" aria-live="polite">
        {messages.map((message) => (
          <ChatMessage key={message.id} message={message} />
        ))}
        {isSending && <div className="ai-chat-panel__thinking">Thinking…</div>}
      </div>

      <form
        className="ai-chat-panel__composer"
        onSubmit={(event) => {
          event.preventDefault();
          void handleSend();
        }}
      >
        <input
          type="text"
          placeholder='e.g. "Add a 3m wall on the north side"'
          value={draft}
          disabled={isSending}
          onChange={(event) => setDraft(event.target.value)}
        />
        <button type="submit" disabled={isSending || draft.trim().length === 0}>
          Send
        </button>
      </form>
    </div>
  );
}
