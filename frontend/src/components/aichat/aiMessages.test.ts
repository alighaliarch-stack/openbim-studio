/**
 * Day-40 tests for components/aichat/aiMessages.ts (no bundler, no browser).
 *
 * Run from frontend/:
 *     node --test src/components/aichat/aiMessages.test.ts
 */

import assert from "node:assert/strict";
import fs from "node:fs";
import { test } from "node:test";

import {
  AI_EXAMPLE_PROMPT,
  REJECTION_FALLBACK,
  REJECTION_MESSAGES,
  describeAiReply,
} from "./aiMessages.ts";

const CONTRACT = new URL("../../../../backend/services/commands/contract.py", import.meta.url);

/** The RejectionReason values the backend can send (parsed from its enum). */
function backendReasonCodes(): string[] {
  const source = fs.readFileSync(CONTRACT, "utf8");
  const body = source.split("class RejectionReason")[1].split("\nclass ")[0];
  return [...body.matchAll(/^\s+[A-Z_]+ = "([a-z_]+)"/gm)].map((m) => m[1]);
}

test("example prompt is a single millimetre size", () => {
  assert.equal(AI_EXAMPLE_PROMPT, "Make the selected wall 2800 mm high");
  assert.doesNotMatch(AI_EXAMPLE_PROMPT, /\d\s*m\b/);
});

test("every backend rejection code has a user-facing message", () => {
  const codes = backendReasonCodes();
  assert.ok(codes.length >= 20, `parsed ${codes.length} codes`);
  for (const code of codes) assert.ok(REJECTION_MESSAGES[code], `no message for ${code}`);
});

test("messages contain no internal codes or command names", () => {
  for (const [code, message] of Object.entries(REJECTION_MESSAGES)) {
    assert.ok(!message.includes(code), `${code} message shows its code`);
    assert.doesNotMatch(message, /EditProperty|Qto_|_[a-z]/, `${code} message is technical`);
  }
});

test("value_out_of_range from a metres/millimetres mix-up (Day 38) is explained without the raw detail", () => {
  const reply = describeAiReply(
    "Rejected EditProperty: value_out_of_range (value must be at most 100000 mm, got 3200000.0 mm).\nNo change was proposed.",
  );
  assert.deepEqual(reply.rejections, ["value_out_of_range"]);
  assert.equal(reply.text, `${REJECTION_MESSAGES.value_out_of_range} No change was proposed.`);
  for (const raw of ["value_out_of_range", "3200000", "EditProperty", "Rejected"]) assert.ok(!reply.text.includes(raw), raw);
  assert.match(reply.text, /millimetres/);
});

test("ambiguous_quantity (Day 37 guard) is explained without the raw detail", () => {
  const reply = describeAiReply(
    "Rejected EditProperty: ambiguous_quantity (the request contains '3' besides 200 mm; state exactly one dimension, as digits in mm or m).\n"
      + "No change was proposed.",
  );
  assert.deepEqual(reply.rejections, ["ambiguous_quantity"]);
  assert.equal(reply.text, `${REJECTION_MESSAGES.ambiguous_quantity} No change was proposed.`);
  assert.ok(!reply.text.includes("besides"));
});

test("an unknown code gets the safe fallback and is still recorded", () => {
  const reply = describeAiReply("Rejected EditProperty: some_future_reason (details).\nNo change was proposed.");
  assert.deepEqual(reply.rejections, ["some_future_reason"]);
  assert.equal(reply.text, `${REJECTION_FALLBACK} No change was proposed.`);
});

test("repeated rejections show each message once but keep every code", () => {
  const line = "Rejected EditProperty: target_mismatch (the proposal targets 'b' but the selected wall is 'a').";
  const reply = describeAiReply(`${line}\n${line}\nNo change was proposed.`);
  assert.deepEqual(reply.rejections, ["target_mismatch", "target_mismatch"]);
  assert.equal(reply.text, `${REJECTION_MESSAGES.target_mismatch} No change was proposed.`);
});

test("other lines are kept and joined as sentences", () => {
  const proposal = "Proposed (not applied): set Qto_WallBaseQuantities.Height of 'wall-001' to 2800 mm. Confirm to apply it.";
  assert.deepEqual(describeAiReply(proposal), { text: proposal, rejections: [] });
  // Model text without a full stop (Day 38: "...cannot propose change No change was proposed.").
  assert.deepEqual(describeAiReply("No wall is selected, cannot propose change\nNo change was proposed."),
    { text: "No wall is selected, cannot propose change. No change was proposed.", rejections: [] });
  // Text that only resembles a rejection line is not rewritten.
  assert.equal(describeAiReply("Rejected by the reviewer: see notes").text, "Rejected by the reviewer: see notes.");
});
