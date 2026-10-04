// Stands in for a payment provider's checkout while Guzo runs with the fake provider.
import { api, fill, h } from "/static/common.js";

const app = document.getElementById("app");
const providerRef = location.pathname.split("/").pop();
const back = new URLSearchParams(location.search).get("return");

async function send(status) {
  try {
    await api("POST", location.pathname, { body: { status } });
    fill(app,
      h("div", { class: "card" },
        h("p", {}, status === "paid" ? "Payment recorded." : "Payment marked as failed."),
        back && back.startsWith("/") ? h("a", { class: "button primary", href: back }, "Back to your booking") : null),
    );
  } catch (error) {
    app.append(h("div", { class: "error" }, error.message));
  }
}

app.append(
  h("div", { class: "card" },
    h("p", {}, "No real payment provider is connected. This page plays its part."),
    h("p", { class: "muted" }, `Reference ${providerRef}`),
    h("div", { class: "actions" },
      h("button", { class: "primary", onclick: () => send("paid") }, "Pay successfully"),
      h("button", { onclick: () => send("failed") }, "Fail the payment"))),
);
