const header = document.querySelector("[data-header]");
const copyButton = document.querySelector("[data-copy]");

const updateHeader = () => {
  header?.classList.toggle("is-scrolled", window.scrollY > 32);
};

updateHeader();
window.addEventListener("scroll", updateHeader, { passive: true });

copyButton?.addEventListener("click", async () => {
  const command = copyButton.dataset.copy;
  if (!command) return;

  try {
    await navigator.clipboard.writeText(command);
    copyButton.textContent = "Copied";
    window.setTimeout(() => {
      copyButton.textContent = "Copy";
    }, 1600);
  } catch {
    copyButton.textContent = "Select command";
  }
});
