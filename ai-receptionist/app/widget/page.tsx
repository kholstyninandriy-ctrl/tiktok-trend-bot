import ChatWidget from "@/components/ChatWidget";
import "./widget.css";

/** Rendered inside the iframe that embed.js injects into the host page. */
export default function WidgetPage() {
  return <ChatWidget />;
}
