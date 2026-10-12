/**
 * components/aichat/AIChatPanel.tsx
 *
 * M09 AI Chat Module — frontend panel (SDS Section 4, M09 / SRS Flow 2).
 *
 * Sends free-text prompts to POST /api/ai/prompt and displays the response.
 *
 * Day-31: the backend returns only validated EditProperty proposals (with
 * server-issued `proposal_ids`). Each is shown as a proposal card; nothing
 * is applied until the user clicks "Apply change", which sends only the
 * proposal id and the current selection to POST /api/ai/confirm. The
 * backend re-validates and applies it through the normal atomic property
 * pipeline. The legacy CreateComponent -> POST /api/scad/render branch was
 * removed: the AI path never renders SCAD directly.
 */

import { useRef, useState } from "react";

import { ChatMessage } from "@/components/aichat/ChatMessage";
import { apiClient, useModelContext } from "@/context/ModelContext";
import { describeSaveFailure } from "@/hooks/useModel";
import { useSelection } from "@/hooks/useSelection";
import type { AIResponse, ChatMessage as ChatMessageType, EditProposal } from "@/types/commands";

export function AIChatPanel() {
  const [messages, setMessages] = useState<ChatMessageType[]>([]);
  const [draft, setDraft] = useState("");
  const [isSending, setIsSending] = useState(false);
  // Day-31: id of the proposal being applied. The ref is set synchronously,
  // so a second click before React re-renders cannot submit again.
  const [applyingId, setApplyingId] = useState<string | null>(null);
  const applyingRef = useRef(false);

  const { primaryGuid } = useSelection();
  const { entities, applyConfirmedEntity } = useModelContext();

  async function handleSend() {
    const prompt = draft.trim();
    if (!prompt || isSending) return;

    appendMessage({ role: "user", text: prompt });
    setDraft("");
    setIsSending(true);

    try {
      const response = await apiClient.sendAiPrompt({ prompt, selected_guid: primaryGuid });
      appendMessage({ role: "assistant", text: response.text });
      for (const proposal of toProposals(response)) {
        appendMessage({ role: "assistant", text: "", proposal });
      }
    } catch (err) {
      // SRS Alternative flow: "LLM API fails -> chat shows error, suggests retry".
      const message = err instanceof Error ? err.message : "Unknown error";
      appendMessage({ role: "assistant", text: `Something went wrong: ${message}. Please retry.` });
    } finally {
      setIsSending(false);
    }
  }

  /** Only EditProperty commands with a server-issued id become proposals. */
  function toProposals(response: AIResponse): EditProposal[] {
    const proposals: EditProposal[] = [];
    response.commands.forEach((command, index) => {
      const id = response.proposal_ids?.[index];
      if (command.type !== "EditProperty" || !id) return;
      const targetGuid = String(command.params.target_guid);
      proposals.push({
        id,
        targetGuid,
        targetName: entities[targetGuid]?.name ?? null,
        pset: String(command.params.pset),
        property: String(command.params.property),
        valueMm: Number(command.params.value),
        status: "pending",
      });
    });
    return proposals;
  }

  async function handleApply(messageId: string, proposal: EditProposal) {
    if (applyingRef.current || proposal.status !== "pending") return;
    applyingRef.current = true;
    setApplyingId(proposal.id);
    updateProposal(messageId, { status: "applying" });

    try {
      const response = await apiClient.confirmAiProposal({ proposal_id: proposal.id, selected_guid: primaryGuid });
      applyConfirmedEntity(response.entity.guid, response.entity);
      updateProposal(messageId, { status: "applied", outcome: response.text });
    } catch (err) {
      // Day-28 atomic edits: a rejected or failed confirmation changed nothing.
      updateProposal(messageId, { status: "failed", outcome: await describeSaveFailure(err) });
    } finally {
      applyingRef.current = false;
      setApplyingId(null);
    }
  }

  function updateProposal(messageId: string, patch: Partial<EditProposal>) {
    setMessages((prev) =>
      prev.map((m) => (m.id === messageId && m.proposal ? { ...m, proposal: { ...m.proposal, ...patch } } : m))
    );
  }

  function appendMessage(partial: Omit<ChatMessageType, "id" | "timestamp">) {
    setMessages((prev) => [
      ...prev,
      { ...partial, id: crypto.randomUUID(), timestamp: new Date().toISOString() },
    ]);
  }

  const busy = isSending || applyingId !== null;

  return (
    <div className="ai-chat-panel">
      <div className="ai-chat-panel__messages" aria-live="polite">
        {messages.map((message) => (
          <ChatMessage
            key={message.id}
            message={message}
            selectedGuid={primaryGuid}
            applyDisabled={busy}
            onApply={(proposal) => void handleApply(message.id, proposal)}
            onDismiss={() => updateProposal(message.id, { status: "dismissed" })}
          />
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
          placeholder='e.g. "Make the selected wall 3.2 m high"'
          value={draft}
          disabled={busy}
          onChange={(event) => setDraft(event.target.value)}
        />
        <button type="submit" disabled={busy || draft.trim().length === 0}>
          Send
        </button>
      </form>
    </div>
  );
}
