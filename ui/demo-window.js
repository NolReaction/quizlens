"use strict";

// A named window lets the user return to a running question without restarting its timer.
for (const link of document.querySelectorAll("a[data-demo-window]")) {
  link.addEventListener("click", event => {
    if (event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;

    const width = Math.min(960, window.screen.availWidth);
    const height = Math.min(800, window.screen.availHeight);
    let popup;
    try {
      popup = window.open("", link.target, `popup=yes,width=${width},height=${height},resizable=yes,scrollbars=yes`);
      if (popup && !popup.closed) {
        if (popup.location.href === "about:blank") popup.location.replace(link.href);
        else if (popup.location.origin !== window.location.origin || popup.location.pathname !== "/demo") return;
        event.preventDefault();
        popup.focus();
        return;
      }
    } catch {
      // The browser can still handle the ordinary link, including its own tab preferences.
    }
    // Keep the normal link active if a browser declines a scripted popup.
    if (!link.parentElement.querySelector(".demo-window-help")) {
      const help = document.createElement("small");
      help.className = "demo-window-help";
      help.setAttribute("role", "status");
      help.textContent = "Если окно не появилось, открой ссылку «Пробный квиз» в новой вкладке через меню браузера.";
      link.insertAdjacentElement("afterend", help);
    }
  });
}
