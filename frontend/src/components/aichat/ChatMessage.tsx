/**
 * components/aichat/ChatMessage.tsx
 *
 * M09 AI Chat Module — single message bubble (user or assistant).
 */

import type { ChatMessage as ChatMessageType } from "@/types/commands";

export function ChatMessage({ message }: { message: ChatMessageType }) {
  return (
    <div className={`chat-message chat-message--${message.role}`}>
      <span className="chat-message__text">{message.text}</span>
    </div>
  );
}
