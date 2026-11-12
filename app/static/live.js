// Live dashboard: new reports arrive through Server-Sent Events (/events),
// fed by Redis Pub/Sub. Every value is inserted with textContent, never
// innerHTML, so a report containing HTML is shown as text and cannot run.
(function () {
  "use strict";

  const list = document.getElementById("report-list");
  const status = document.getElementById("live-status");
  if (!list || !status || !window.EventSource) return;

  const label = status.querySelector(".live-label");
  const empty = document.getElementById("empty-state");
  const limit = Number(list.dataset.limit) || 10;

  function setStatus(state, text) {
    status.dataset.state = state;
    label.textContent = text;
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function bump(id) {
    const node = document.getElementById(id);
    if (node) node.textContent = String(Number(node.textContent) + 1);
  }

  function addReport(report) {
    const item = el("li", "report report-new");
    const head = el("div", "report-head");
    head.append(el("h3", "", report.title));
    head.append(el("span", "badge sev-" + report.severity, report.severity_label));
    const when = new Date(report.created_at);
    const stamp = when.toISOString().slice(0, 16).replace("T", " ") + " UTC";
    item.append(
      head,
      el("p", "report-meta", "#" + report.id + " · " + report.category_label + " · " + stamp),
      el("p", "report-text", report.description)
    );
    list.prepend(item);
    while (list.children.length > limit) list.lastElementChild.remove();
    if (empty) empty.hidden = true;
    bump("count-total");
    bump("count-" + report.severity);
  }

  const source = new EventSource("/events");
  source.addEventListener("open", () => setStatus("on", "Live"));
  source.addEventListener("error", () => setStatus("off", "Reconnecting…"));
  source.addEventListener("report", (event) => {
    try {
      addReport(JSON.parse(event.data));
    } catch (err) {
      console.error("Invalid live event", err);
    }
  });
})();
