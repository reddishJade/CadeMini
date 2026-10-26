(() => {
  const body = document.body;
  const labels = {
    copy: body.dataset.copyLabel || "Copy",
    copied: body.dataset.copiedLabel || "Copied",
    select: body.dataset.selectLabel || "Select",
    dark: body.dataset.darkLabel || "Dark",
    light: body.dataset.lightLabel || "Light",
    switchDark: body.dataset.switchDarkLabel || "Switch to dark theme",
    switchLight: body.dataset.switchLightLabel || "Switch to light theme",
  };

  const themeButton = document.getElementById("theme");
  function applyTheme(next) {
    document.documentElement.dataset.theme = next;
    if (!themeButton) return;
    themeButton.textContent = next === "dark" ? labels.light : labels.dark;
    themeButton.setAttribute(
      "aria-label",
      next === "dark" ? labels.switchLight : labels.switchDark,
    );
  }

  if (themeButton) {
    themeButton.addEventListener("click", () => {
      const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
      applyTheme(next);
      try {
        localStorage.setItem("cade-theme", next);
      } catch (error) {
        // Storage can be unavailable in private or locked-down contexts.
      }
    });
    let saved = null;
    try {
      saved = localStorage.getItem("cade-theme");
    } catch (error) {
      saved = null;
    }
    const preferred = window.matchMedia("(prefers-color-scheme: dark)").matches
      ? "dark"
      : "light";
    applyTheme(saved === "dark" || saved === "light" ? saved : preferred);
  }

  document.querySelectorAll("[data-copy]").forEach((button) => {
    button.textContent = labels.copy;
    button.addEventListener("click", async () => {
      const target = document.querySelector(button.dataset.copy);
      const text = target ? target.textContent : "";
      try {
        await navigator.clipboard.writeText(text);
        button.textContent = labels.copied;
      } catch (error) {
        button.textContent = labels.select;
      }
      window.setTimeout(() => {
        button.textContent = labels.copy;
      }, 1600);
    });
  });

  const tabs = Array.from(document.querySelectorAll("[role='tab']"));
  const panels = Array.from(document.querySelectorAll("[role='tabpanel']"));
  function selectTab(tab) {
    const selectedPanel = tab.getAttribute("aria-controls");
    tabs.forEach((item) => {
      const active = item === tab;
      item.setAttribute("aria-selected", active ? "true" : "false");
      item.tabIndex = active ? 0 : -1;
    });
    panels.forEach((panel) => {
      panel.hidden = panel.id !== selectedPanel;
    });
  }

  tabs.forEach((tab, index) => {
    tab.addEventListener("click", () => selectTab(tab));
    tab.addEventListener("keydown", (event) => {
      let nextIndex = index;
      if (event.key === "ArrowDown" || event.key === "ArrowRight") {
        nextIndex = (index + 1) % tabs.length;
      } else if (event.key === "ArrowUp" || event.key === "ArrowLeft") {
        nextIndex = (index - 1 + tabs.length) % tabs.length;
      } else if (event.key === "Home") {
        nextIndex = 0;
      } else if (event.key === "End") {
        nextIndex = tabs.length - 1;
      } else {
        return;
      }
      event.preventDefault();
      selectTab(tabs[nextIndex]);
      tabs[nextIndex].focus();
    });
  });
  if (tabs.length) {
    selectTab(tabs.find((tab) => tab.getAttribute("aria-selected") === "true") || tabs[0]);
  }

  const navLinks = Array.from(document.querySelectorAll(".topnav a[href^='#']"));
  const sections = navLinks
    .map((link) => document.querySelector(link.getAttribute("href")))
    .filter(Boolean);
  if ("IntersectionObserver" in window && sections.length) {
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((entry) => entry.isIntersecting)
          .sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
        if (!visible) return;
        navLinks.forEach((link) => {
          link.classList.toggle(
            "is-active",
            link.getAttribute("href") === `#${visible.target.id}`,
          );
        });
      },
      { rootMargin: "-20% 0px -65% 0px", threshold: [0, 0.1, 0.5] },
    );
    sections.forEach((section) => observer.observe(section));
  }
})();
