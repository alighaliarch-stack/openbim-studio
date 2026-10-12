/**
 * components/aichat/ChatMessage.tsx
 *
 * M09 AI Chat Module — single message bubble (user or assistant).
 *
 * Day-31: a message carrying an EditProperty proposal renders a proposal
 * card (target wall, property, value in mm) whose state is explicit:
 * proposed (not applied) / applying / applied / not applied / dismissed.
 */

import type { ChatMessage as ChatMessageType, EditProposal } from "@/types/commands";

interface ChatMessageProps {
  message: ChatMessageType;
  selectedGuid?: string | null;
  applyDisabled?: boolean;
  onApply?: (proposal: EditProposal) => void;
  onDismiss?: () => void;
}

export function ChatMessage({ message, selectedGuid, applyDisabled, onApply, onDismiss }: ChatMessageProps) {
  const { proposal } = message;
  return (
    <div className={`chat-message chat-message--${message.role}`}>
      {message.text && <span className="chat-message__text">{message.text}</span>}
      {proposal && (
        <ProposalCard
          proposal={proposal}
          selectedGuid={selectedGuid ?? null}
          applyDisabled={Boolean(applyDisabled)}
          onApply={() => onApply?.(proposal)}
          onDismiss={() => onDismiss?.()}
        />
      )}
    </div>
  );
}

const STATUS_LABEL: Record<EditProposal["status"], string> = {
  pending: "Proposed change — not applied",
  applying: "Applying…",
  applied: "Applied",
  failed: "Not applied",
  dismissed: "Dismissed — not applied",
};

function ProposalCard({
  proposal, selectedGuid, applyDisabled, onApply, onDismiss,
}: {
  proposal: EditProposal;
  selectedGuid: string | null;
  applyDisabled: boolean;
  onApply: () => void;
  onDismiss: () => void;
}) {
  // The backend enforces this too; the hint just explains a disabled button.
  const targetSelected = selectedGuid === proposal.targetGuid;
  const wall = proposal.targetName ? `${proposal.targetName} (${proposal.targetGuid})` : proposal.targetGuid;

  return (
    <div className={`ai-proposal ai-proposal--${proposal.status}`}>
      <div className="ai-proposal__status">{STATUS_LABEL[proposal.status]}</div>
      <dl className="ai-proposal__details">
        <dt>Wall</dt>
        <dd>{wall}</dd>
        <dt>Property</dt>
        <dd>{proposal.pset}.{proposal.property}</dd>
        <dt>New value</dt>
        <dd>{proposal.valueMm} mm</dd>
      </dl>
      {proposal.outcome && <div className="ai-proposal__outcome">{proposal.outcome}</div>}
      {proposal.status === "pending" && (
        <div className="ai-proposal__actions">
          <button type="button" onClick={onApply} disabled={applyDisabled || !targetSelected}>
            Apply change
          </button>
          <button type="button" onClick={onDismiss} disabled={applyDisabled}>
            Dismiss
          </button>
          {!targetSelected && <span className="ai-proposal__hint">Select this wall to apply the change.</span>}
        </div>
      )}
    </div>
  );
}
