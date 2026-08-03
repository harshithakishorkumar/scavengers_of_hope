// Open the verdict UI as a right-docked side panel instead of a floating popup,
// so it never covers the campaign page it is describing.
chrome.sidePanel
  .setPanelBehavior({ openPanelOnActionClick: true })
  .catch((err) => console.error("sidePanel.setPanelBehavior:", err));

// Re-run the lookup when the user switches tabs or navigates while the panel
// is open; the panel document stays alive across navigations, so it has to be
// told that the active URL changed.
function notifyPanel() {
  chrome.runtime.sendMessage({ type: "tab-changed" }).catch(() => {});
}

chrome.tabs.onActivated.addListener(notifyPanel);
chrome.tabs.onUpdated.addListener((_id, info) => {
  if (info.status === "complete" || info.url) notifyPanel();
});
