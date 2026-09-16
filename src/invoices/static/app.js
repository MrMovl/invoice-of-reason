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
