import Anthropic from "@anthropic-ai/sdk";
import { GREETING, SYSTEM_PROMPT } from "./prompt";
import { runTool, TOOLS } from "./tools";
import type { ChatMessage } from "./types";

const MAX_TOOL_ROUNDS = 5;

export function isClaudeConfigured(): boolean {
  return Boolean(process.env.ANTHROPIC_API_KEY);
}

let client: Anthropic | null = null;

function anthropic(): Anthropic {
  if (!client) client = new Anthropic();
  return client;
}

/** Runs the receptionist turn, including any tool calls, and returns its reply. */
export async function respond(
  conversationId: string,
  history: ChatMessage[],
): Promise<string> {
  if (!isClaudeConfigured()) return demoReply(history);

  const messages: Anthropic.MessageParam[] = history.map((m) => ({
    role: m.role,
    content: m.content,
  }));

  for (let round = 0; round < MAX_TOOL_ROUNDS; round++) {
    const response = await anthropic().messages.create({
      model: process.env.CLAUDE_MODEL || "claude-opus-5",
      max_tokens: 1024,
      // Chat latency matters more than depth here; low effort keeps replies snappy.
      output_config: { effort: "low" },
      system: [
        { type: "text", text: SYSTEM_PROMPT, cache_control: { type: "ephemeral" } },
      ],
      tools: TOOLS,
      messages,
    });

    if (response.stop_reason === "refusal") {
      return "Sorry — I can't help with that one. Would you like me to have an agent call you?";
    }

    const toolUses = response.content.filter(
      (b): b is Anthropic.ToolUseBlock => b.type === "tool_use",
    );

    if (toolUses.length === 0) {
      const text = response.content
        .filter((b): b is Anthropic.TextBlock => b.type === "text")
        .map((b) => b.text)
        .join("\n")
        .trim();
      return text || GREETING;
    }

    messages.push({ role: "assistant", content: response.content });

    const results: Anthropic.ToolResultBlockParam[] = await Promise.all(
      toolUses.map(async (call) => ({
        type: "tool_result" as const,
        tool_use_id: call.id,
        content: await runTool(
          call.name,
          call.input as Record<string, unknown>,
          conversationId,
        ),
      })),
    );

    // All tool results must go back in a single user message.
    messages.push({ role: "user", content: results });
  }

  return "Let me get an agent to pick this up with you — what's the best number to reach you on?";
}

/**
 * Scripted stand-in used when ANTHROPIC_API_KEY is missing, so the widget still
 * demonstrates the qualification flow with no keys configured.
 */
function demoReply(history: ChatMessage[]): string {
  const turns = history.filter((m) => m.role === "user").length;
  const script = [
    "Happy to help with that. What part of the city are you looking in?",
    "Good area. What price range are you working with?",
    "That works for a few of our listings. How soon are you hoping to move?",
    "Got it. I can set you up with an agent for a walkthrough — what's your name and the best number to reach you?",
    "Perfect, thank you. I have Saturday at 11am or Tuesday at 2pm open — which suits you better?",
    "Booked. You'll get a confirmation shortly and your agent will follow up with the details.",
  ];
  return (
    script[Math.min(turns - 1, script.length - 1)] ??
    "Thanks — an agent will follow up shortly."
  );
}
