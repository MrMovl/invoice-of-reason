// Fill a known customer's address when their name is picked from the list.
document.addEventListener("DOMContentLoaded", () => {
  const nameInput = document.querySelector("[data-customer-autofill]");
  if (!nameInput) return;
  const form = nameInput.form;
  nameInput.addEventListener("change", () => {
    const match = [...document.querySelectorAll("#customers option")]
      .find((o) => o.value === nameInput.value);
    if (!match) return;
    for (const [field, key] of [["customer_street", "street"], ["customer_city", "city"]]) {
      const input = form.elements[field];
      if (input && !input.value) input.value = match.dataset[key];
    }
  });
});

// Open <dialog> elements from buttons with data-dialog-open="<dialog id>".
document.addEventListener("click", (event) => {
  const trigger = event.target.closest("[data-dialog-open]");
  if (!trigger) return;
  const dialog = document.getElementById(trigger.dataset.dialogOpen);
  if (dialog && typeof dialog.showModal === "function") dialog.showModal();
});

// Invoice positions: add and remove rows and show the running total. Without JS the form keeps
// the single position it is rendered with, which is what most invoices need.
document.addEventListener("DOMContentLoaded", () => {
  const list = document.getElementById("items");
  const template = document.getElementById("item-template");
  const add = document.getElementById("item-add");
  const total = document.getElementById("items-total");
  if (!list || !template || !add) return;
  const euro = new Intl.NumberFormat("de-DE", { style: "currency", currency: "EUR" });
  const rows = () => [...list.querySelectorAll("[data-item]")];

  // Same inputs as archive.parse_amount accepts; display only, the server parses again.
  const amountOf = (raw) => {
    let s = raw.trim().replace(/[€\s]/g, "");
    if (s.includes(",") && s.includes(".")) {
      s = s.lastIndexOf(",") > s.lastIndexOf(".")
        ? s.replace(/\./g, "").replace(",", ".")
        : s.replace(/,/g, "");
    } else if (s.includes(",")) {
      s = s.replace(",", ".");
    }
    const value = Number(s);
    return Number.isFinite(value) ? value : 0;
  };

  const refresh = () => {
    const all = rows();
    all.forEach((row, index) => {
      row.querySelector("[data-item-number]").textContent = index + 1;
      row.querySelector("[data-item-remove]").hidden = all.length < 2;
    });
    if (!total) return;
    const sum = all.reduce((acc, row) => acc + amountOf(row.querySelector("[data-item-amount]").value), 0);
    total.hidden = all.length < 2;
    total.textContent = `Gesamtbetrag ${euro.format(sum)}`;
  };

  add.hidden = false;
  add.addEventListener("click", () => {
    list.append(template.content.cloneNode(true));
    refresh();
    rows().at(-1).querySelector("input").focus();
  });
  list.addEventListener("click", (event) => {
    const row = event.target.closest("[data-item-remove]") && event.target.closest("[data-item]");
    if (!row || rows().length < 2) return;
    row.remove();
    refresh();
  });
  list.addEventListener("input", (event) => {
    if (event.target.matches("[data-item-amount]")) refresh();
  });
  refresh();
});
