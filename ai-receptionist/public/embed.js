/**
 * AI Receptionist — site embed.
 *
 * Drop one line onto any page:
 *   <script src="https://YOUR-APP.vercel.app/embed.js" async></script>
 *
 * The widget runs inside an iframe so the host site's CSS and our CSS can
 * never collide. The iframe stays bubble-sized until the visitor opens it.
 */
(function () {
  if (window.__aiReceptionistLoaded) return;
  window.__aiReceptionistLoaded = true;

  var script = document.currentScript;
  var origin = script
    ? new URL(script.src, window.location.href).origin
    : window.location.origin;

  var COLLAPSED = { width: "88px", height: "88px" };
  var EXPANDED = { width: "396px", height: "620px" };

  var frame = document.createElement("iframe");
  frame.src = origin + "/widget";
  frame.title = "Chat with the front desk";
  frame.allow = "microphone";
  frame.setAttribute("aria-label", "Chat with the front desk");

  var s = frame.style;
  s.position = "fixed";
  s.bottom = "0";
  s.right = "0";
  s.border = "0";
  s.zIndex = "2147483000";
  s.colorScheme = "normal";
  s.background = "transparent";
  s.width = COLLAPSED.width;
  s.height = COLLAPSED.height;
  s.maxWidth = "100vw";
  s.maxHeight = "100vh";
  s.transition = "width .18s ease, height .18s ease";

  function apply(open) {
    var narrow = window.innerWidth < 460;
    if (open && narrow) {
      s.width = "100vw";
      s.height = "100vh";
    } else if (open) {
      s.width = EXPANDED.width;
      s.height = EXPANDED.height;
    } else {
      s.width = COLLAPSED.width;
      s.height = COLLAPSED.height;
    }
  }

  var isOpen = false;

  window.addEventListener("message", function (event) {
    if (event.origin !== origin) return;
    var data = event.data;
    if (!data || data.type !== "ar:resize") return;
    isOpen = Boolean(data.open);
    apply(isOpen);
  });

  window.addEventListener("resize", function () {
    apply(isOpen);
  });

  function mount() {
    document.body.appendChild(frame);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mount);
  } else {
    mount();
  }
})();
