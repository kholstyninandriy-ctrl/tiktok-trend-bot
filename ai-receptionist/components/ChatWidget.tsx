"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { ChatMessage } from "@/lib/types";

const GREETING =
  "Hi, thanks for stopping by Coastal Realty Group. Are you looking to buy, sell, or just getting a feel for the market?";

type VoiceState = "idle" | "connecting" | "live";

export default function ChatWidget() {
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([
    { role: "assistant", content: GREETING },
  ]);
  const [input, setInput] = useState("");
  const [thinking, setThinking] = useState(false);
  const [voice, setVoice] = useState<VoiceState>("idle");

  const conversationId = useRef<string>("");
  const logRef = useRef<HTMLDivElement>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const vapiRef = useRef<any>(null);

  if (!conversationId.current && typeof crypto !== "undefined") {
    conversationId.current = crypto.randomUUID();
  }

  const voiceEnabled = Boolean(process.env.NEXT_PUBLIC_VAPI_PUBLIC_KEY);

  // Tell the host page to grow or shrink the iframe.
  useEffect(() => {
    window.parent?.postMessage({ type: "ar:resize", open }, "*");
  }, [open]);

  useEffect(() => {
    logRef.current?.scrollTo({
      top: logRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [messages, thinking]);

  const send = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || thinking) return;

      const next: ChatMessage[] = [...messages, { role: "user", content: trimmed }];
      setMessages(next);
      setInput("");
      setThinking(true);

      try {
        const res = await fetch("/api/chat", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            conversationId: conversationId.current,
            messages: next,
            sourceUrl: document.referrer || null,
          }),
        });

        const data = (await res.json()) as { reply?: string; error?: string };
        setMessages([
          ...next,
          {
            role: "assistant",
            content:
              data.reply ??
              "Sorry, something went wrong on my end. Could you try that again?",
          },
        ]);
      } catch {
        setMessages([
          ...next,
          {
            role: "assistant",
            content:
              "I lost the connection there. Give me one moment and try again.",
          },
        ]);
      } finally {
        setThinking(false);
      }
    },
    [messages, thinking],
  );

  const toggleVoice = useCallback(async () => {
    if (voice === "live") {
      vapiRef.current?.stop();
      setVoice("idle");
      return;
    }

    setVoice("connecting");
    try {
      const { default: Vapi } = await import("@vapi-ai/web");
      if (!vapiRef.current) {
        vapiRef.current = new Vapi(process.env.NEXT_PUBLIC_VAPI_PUBLIC_KEY!);
        vapiRef.current.on("call-start", () => setVoice("live"));
        vapiRef.current.on("call-end", () => setVoice("idle"));
        vapiRef.current.on("error", () => setVoice("idle"));
      }
      await vapiRef.current.start(process.env.NEXT_PUBLIC_VAPI_ASSISTANT_ID, {
        metadata: { conversationId: conversationId.current },
      });
    } catch {
      setVoice("idle");
      setMessages((m) => [
        ...m,
        {
          role: "assistant",
          content:
            "I couldn't start the voice call — let's keep going here instead.",
        },
      ]);
    }
  }, [voice]);

  if (!open) {
    return (
      <div className="w-root">
        <button
          className="w-bubble"
          onClick={() => setOpen(true)}
          aria-label="Chat with the front desk"
        >
          <svg width="26" height="26" viewBox="0 0 24 24" fill="none">
            <path
              d="M21 11.5a8.4 8.4 0 0 1-9 8.4 9 9 0 0 1-3.6-.7L3 21l1.9-4.9A8.2 8.2 0 0 1 3.6 11.5 8.4 8.4 0 0 1 12 3a8.4 8.4 0 0 1 9 8.5Z"
              stroke="currentColor"
              strokeWidth="1.7"
              strokeLinejoin="round"
            />
          </svg>
        </button>
      </div>
    );
  }

  return (
    <div className="w-root">
      <div className="w-panel">
        <header className="w-head">
          <div className="w-avatar">AV</div>
          <div>
            <div className="w-head-name">Avery · Front desk</div>
            <div className="w-head-sub">
              {voice === "live" ? "On a call" : "Typically replies instantly"}
            </div>
          </div>
          <div className="w-head-actions">
            {voiceEnabled && (
              <button
                className={`w-icon-btn${voice === "live" ? " live" : ""}`}
                onClick={toggleVoice}
                aria-label={voice === "live" ? "End call" : "Start voice call"}
                title={voice === "live" ? "End call" : "Talk instead of typing"}
              >
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none">
                  <path
                    d="M6.6 10.8a15.1 15.1 0 0 0 6.6 6.6l2.2-2.2a1 1 0 0 1 1-.24 11.4 11.4 0 0 0 3.6.58 1 1 0 0 1 1 1V20a1 1 0 0 1-1 1A17 17 0 0 1 3 4a1 1 0 0 1 1-1h3.5a1 1 0 0 1 1 1c0 1.25.2 2.46.57 3.6a1 1 0 0 1-.25 1l-2.22 2.2Z"
                    fill="currentColor"
                  />
                </svg>
              </button>
            )}
            <button
              className="w-icon-btn"
              onClick={() => setOpen(false)}
              aria-label="Close chat"
            >
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none">
                <path
                  d="M6 6l12 12M18 6L6 18"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                />
              </svg>
            </button>
          </div>
        </header>

        <div className="w-log" ref={logRef}>
          {messages.map((m, i) => (
            <div key={i} className={`w-msg ${m.role === "user" ? "me" : "bot"}`}>
              {m.content}
            </div>
          ))}
          {thinking && (
            <div className="w-msg bot w-typing">
              <i />
              <i />
              <i />
            </div>
          )}
        </div>

        {voice !== "idle" && (
          <div className="w-voice">
            <b>
              {voice === "connecting" ? "Connecting…" : "Voice call in progress"}
            </b>
            <span>· tap the phone icon to hang up</span>
          </div>
        )}

        <form
          className="w-form"
          onSubmit={(e) => {
            e.preventDefault();
            void send(input);
          }}
        >
          <input
            className="w-input"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Ask about a listing, price, or neighborhood…"
            autoFocus
          />
          <button className="w-send" disabled={thinking || !input.trim()}>
            <svg width="17" height="17" viewBox="0 0 24 24" fill="none">
              <path
                d="M4 12l16-8-6 8 6 8-16-8Z"
                fill="currentColor"
              />
            </svg>
          </button>
        </form>
        <div className="w-foot">Powered by AI Receptionist</div>
      </div>
    </div>
  );
}
