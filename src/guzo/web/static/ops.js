// The operations console. It holds no rules of its own: every action is a call to
// /v1/ops, which checks the role, applies the policy and writes the audit log.
import { ApiError, addisTime, api, field, fill, h, money, ref, toMinor } from "/static/common.js";

const app = document.getElementById("app");
const nav = document.getElementById("nav");
const STATUSES = ["quoted", "awaiting_payment", "confirmed", "assigned", "en_route", "arrived",
  "in_progress", "completed", "cancelled", "expired", "no_show"];
const TABS = { bookings: "Bookings", drivers: "Drivers", prices: "Prices", partners: "Partners", audit: "Audit log" };

const state = {
  token: sessionStorage.getItem("guzo.ops.token"),
  tab: "bookings",
  statusFilter: "confirmed",
  bookingId: null,
  error: null,
};

const call = (method, path, body) => api(method, path, { token: state.token, body });

function show(...nodes) {
  fill(app, state.error ? h("div", { class: "error", role: "alert" }, state.error) : null, nodes);
}

function busy(handler) {
  return async (event) => {
    event.preventDefault();
    const button = event.submitter || event.currentTarget;
    button.disabled = true;
    state.error = null;
    try {
      await handler(event);
    } catch (error) {
      if (error.status === 401) return signOut();
      state.error = error instanceof ApiError ? error.message : String(error);
    } finally {
      button.disabled = false;
    }
    render();
  };
}

function signOut() {
  sessionStorage.removeItem("guzo.ops.token");
  state.token = null;
  render();
}

function table(headers, rows) {
  return h("div", { class: "table-wrap" },
    h("table", {}, h("thead", {}, h("tr", {}, headers.map((x) => h("th", {}, x)))), h("tbody", {}, rows)));
}

function text(id, attrs = {}) {
  return h("input", { id, name: id, ...attrs });
}

function choose(id, options, selected) {
  return h("select", { id, name: id },
    options.map(([value, label]) => h("option", { value, selected: value === selected }, label)));
}

const formValues = (event) => Object.fromEntries(new FormData(event.target.closest("form") || event.target));

// --- Sign in ------------------------------------------------------------------

function renderLogin() {
  nav.replaceChildren();
  show(
    h("h1", {}, "Sign in"),
    h("form", {
      class: "card",
      onsubmit: busy(async (event) => {
        const { email, password, totp_code } = formValues(event);
        const result = await api("POST", "/v1/auth/ops/login", { body: { email, password, totp_code } });
        state.token = result.access_token;
        sessionStorage.setItem("guzo.ops.token", state.token);
      }),
    },
      field("Email", text("email", { type: "email", required: true, autocomplete: "username" })),
      field("Password", text("password", { type: "password", required: true, autocomplete: "current-password" })),
      field("Authenticator code", text("totp_code", { required: true, inputmode: "numeric", pattern: "\\d{6}", maxlength: 6, autocomplete: "one-time-code" })),
      h("div", { class: "actions" }, h("button", { class: "primary", type: "submit" }, "Sign in"))),
  );
}

// --- Bookings -----------------------------------------------------------------

async function renderBookings() {
  const query = state.statusFilter ? `?status=${state.statusFilter}` : "";
  const bookings = await call("GET", `/v1/ops/bookings${query}`);
  const filter = choose("status-filter", [["", "All"], ...STATUSES.map((s) => [s, s.replace("_", " ")])], state.statusFilter);
  filter.addEventListener("change", () => { state.statusFilter = filter.value; render(); });
  show(
    h("h1", {}, "Bookings"),
    h("div", { class: "row" }, field("Status", filter)),
    bookings.length === 0 ? h("p", { class: "muted" }, "No bookings with this status.") : table(
      ["Ref", "Pickup time (Addis)", "Status", "From", "To", "Vehicle", "Riders", "Price"],
      bookings.map((b) => h("tr", { class: "clickable", onclick: () => { state.bookingId = b.id; render(); } },
        h("td", {}, ref(b.id)),
        h("td", {}, addisTime(b.scheduled_at)),
        h("td", {}, h("span", { class: "status" }, b.status.replace("_", " "))),
        h("td", {}, b.pickup.label),
        h("td", {}, b.dropoff.label),
        h("td", {}, b.vehicle_class),
        h("td", {}, b.riders.map((r) => r.name).join(", ")),
        h("td", {}, money(b.price))))),
  );
}

async function renderBooking() {
  const id = state.bookingId;
  const [booking, cash, trail, drivers] = await Promise.all([
    call("GET", `/v1/ops/bookings/${id}`),
    call("GET", `/v1/ops/bookings/${id}/money`),
    call("GET", `/v1/ops/audit?target_type=booking&target_id=${id}`),
    call("GET", "/v1/ops/drivers?verified=true"),
  ]);
  const act = (path, body) => busy(async (event) => {
    await call("POST", `/v1/ops/bookings/${id}/${path}`, body ? body(formValues(event)) : {});
  });
  const reason = (id_) => text(id_, { required: true, maxlength: 300, placeholder: "Reason (recorded in the audit log)" });
  const cars = drivers.flatMap((d) => d.vehicles.filter((v) => v.active).map((v) =>
    [`${d.driver.id}:${v.id}`, `${d.driver.name || d.driver.phone} — ${v.color} ${v.make} ${v.model} (${v.vehicle_class}), ${v.plate}`]));

  const controls = [];
  if (booking.status === "confirmed") {
    controls.push(h("form", { class: "card", onsubmit: act("assign", (v) => {
      const [driver_id, vehicle_id] = v.car.split(":");
      return { driver_id, vehicle_id };
    }) },
      h("h2", {}, "Assign a driver"),
      cars.length ? field("Verified driver and car", choose("car", cars)) : h("p", { class: "muted" }, "No verified driver has a car in service. Add one under Drivers."),
      h("div", { class: "actions" }, h("button", { class: "primary", type: "submit", disabled: !cars.length }, "Assign"))));
  }
  if (booking.status === "assigned") {
    controls.push(h("form", { class: "card", onsubmit: act("unassign", (v) => ({ reason: v.unassign_reason })) },
      h("h2", {}, "Take the job back"),
      field("Reason", reason("unassign_reason")),
      h("div", { class: "actions" }, h("button", { type: "submit" }, "Unassign driver"))));
  }
  if (["confirmed", "assigned"].includes(booking.status)) {
    controls.push(h("form", { class: "card", onsubmit: act("cancel", (v) => ({ reason: v.cancel_reason })) },
      h("h2", {}, "Cancel"),
      h("p", { class: "muted" }, `The refund follows this booking's terms: free cancellation up to ${booking.policy.free_cancel_hours} h before pickup, then a ${booking.policy.late_cancel_fee_pct}% fee.`),
      field("Reason", reason("cancel_reason")),
      h("div", { class: "actions" }, h("button", { class: "danger", type: "submit" }, "Cancel booking and refund per policy"))));
  }
  if (booking.status === "arrived") {
    controls.push(h("form", { class: "card", onsubmit: act("no-show") },
      h("h2", {}, "Rider not found"),
      h("p", { class: "muted" }, `No-show fee for this booking: ${booking.policy.no_show_fee_pct}% of the fare.`),
      h("div", { class: "actions" }, h("button", { class: "danger", type: "submit" }, "Mark as no-show"))));
  }

  const assigned = booking.assignment && drivers.find((d) => d.driver.id === booking.assignment.driver_id);
  show(
    h("button", { class: "link", onclick: () => { state.bookingId = null; render(); } }, "← All bookings"),
    h("h1", {}, `Booking ${ref(booking.id)} `, h("span", { class: "status" }, booking.status.replace("_", " "))),
    h("div", { class: "card" }, h("dl", {},
      h("dt", {}, "Pickup"), h("dd", {}, `${booking.pickup.label}${booking.pickup.zone_id ? ` (${booking.pickup.zone_id})` : ""}`),
      h("dt", {}, "Drop-off"), h("dd", {}, `${booking.dropoff.label}${booking.dropoff.zone_id ? ` (${booking.dropoff.zone_id})` : ""}`),
      h("dt", {}, "Time"), h("dd", {}, `${addisTime(booking.scheduled_at)} Addis Ababa`),
      h("dt", {}, "Flight"), h("dd", {}, booking.flight ? booking.flight.number : "—"),
      h("dt", {}, "Vehicle"), h("dd", {}, `${booking.vehicle_class}, riders: ${booking.seats}, bags: ${booking.bags}`),
      h("dt", {}, "Riders"), h("dd", {}, booking.riders.map((r) => `${r.name} ${r.phone}${r.is_booker ? " (booker)" : ""}`).join("; ")),
      h("dt", {}, "Price"), h("dd", {}, money(booking.price)),
      h("dt", {}, "Partner"), h("dd", {}, booking.partner_code || "—"),
      h("dt", {}, "Driver"), h("dd", {}, booking.assignment
        ? `${assigned ? assigned.driver.name || assigned.driver.phone : booking.assignment.driver_id}${booking.assignment.accepted_at ? ", accepted" : ", not yet accepted"}`
        : "—"))),
    controls,
    h("h2", {}, "Money"),
    cash.payments.length + cash.refunds.length === 0 ? h("p", { class: "muted" }, "No payments yet.") : table(
      ["Type", "Amount", "Status", "Detail"],
      [
        ...cash.payments.map((p) => h("tr", {}, h("td", {}, "Payment"), h("td", {}, money(p.amount)), h("td", {}, p.status), h("td", {}, p.provider))),
        ...cash.refunds.map((r) => h("tr", {}, h("td", {}, "Refund"), h("td", {}, money(r.amount)), h("td", {}, r.status), h("td", {}, r.reason))),
      ]),
    h("h2", {}, "Status history"),
    table(["When (Addis)", "Change", "By", "Reason"], booking.status_history.map((c) => h("tr", {},
      h("td", {}, addisTime(c.at)),
      h("td", {}, `${c.from_status || "—"} → ${c.to_status}`),
      h("td", {}, c.actor),
      h("td", {}, c.reason || "")))),
    h("h2", {}, "Operations actions"),
    trail.length === 0 ? h("p", { class: "muted" }, "None.") : table(["When (Addis)", "Action", "Details"], trail.map((e) => h("tr", {},
      h("td", {}, addisTime(e.at)), h("td", {}, e.action), h("td", {}, JSON.stringify(e.details))))),
  );
}

// --- Drivers ------------------------------------------------------------------

async function renderDrivers() {
  const drivers = await call("GET", "/v1/ops/drivers");
  show(
    h("h1", {}, "Drivers"),
    h("p", { class: "muted" }, "Drivers sign up with their phone number. Verify a driver after vetting; only verified drivers with a car can be assigned."),
    drivers.length === 0 ? h("p", { class: "muted" }, "No drivers have signed up yet.") : null,
    drivers.map(({ driver, vehicles }) => h("div", { class: "card" },
      h("strong", {}, driver.name || "(no name)"), ` ${driver.phone} `,
      h("span", { class: "status" }, driver.is_verified ? "verified" : "not verified"),
      vehicles.length ? h("ul", {}, vehicles.map((v) => h("li", {},
        `${v.color} ${v.make} ${v.model} (${v.vehicle_class}), ${v.plate}${v.active ? "" : ", retired"} `,
        v.active ? h("button", { class: "link", onclick: busy(() => call("DELETE", `/v1/ops/drivers/${driver.id}/vehicles/${v.id}`)) }, "Retire") : null)))
        : h("p", { class: "muted" }, "No car registered."),
      driver.is_verified ? null : h("div", { class: "actions" },
        h("button", { class: "primary", onclick: busy(() => call("POST", `/v1/ops/drivers/${driver.id}/verify`, {})) }, "Mark as verified")),
      h("form", { onsubmit: busy(async (event) => {
        await call("POST", `/v1/ops/drivers/${driver.id}/vehicles`, formValues(event));
      }) },
        h("div", { class: "row" },
          field("Plate", text("plate", { required: true })), field("Make", text("make", { required: true })),
          field("Model", text("model", { required: true })), field("Colour", text("color", { required: true })),
          field("Class", choose("vehicle_class", [["sedan", "sedan"], ["minivan", "minivan"], ["suv", "suv"]]))),
        h("div", { class: "actions" }, h("button", { type: "submit" }, "Add car"))))),
  );
}

// --- Prices -------------------------------------------------------------------

async function renderPrices() {
  const [prices, zones, products] = await Promise.all([
    call("GET", "/v1/ops/catalog/zone-prices"), call("GET", "/v1/catalog/zones"), call("GET", "/v1/catalog/products"),
  ]);
  const product = products.find((p) => p.code === "airport_transfer");
  show(
    h("h1", {}, "Airport zone prices"),
    h("p", { class: "muted" }, "The fixed price between Bole airport and each area. A change applies to new quotes only."),
    prices.length === 0 ? h("p", { class: "muted" }, "No prices published. Bookers cannot get a quote until there is one.") : table(
      ["Area", "Vehicle", "Price", ""],
      prices.map((p) => h("tr", {},
        h("td", {}, (zones.find((z) => z.code === p.zone_id) || {}).name || p.zone_id),
        h("td", {}, p.vehicle_class),
        h("td", {}, money(p.price)),
        h("td", {}, h("button", { class: "link", onclick: busy(() => call("DELETE", `/v1/ops/catalog/zone-prices/${p.id}`)) }, "Withdraw"))))),
    h("form", { class: "card", onsubmit: busy(async (event) => {
      const v = formValues(event);
      const amount = toMinor(v.amount);
      if (amount === null) throw new ApiError(0, "amount", "Enter the price as a number, for example 1200 or 1200.50.");
      await call("PUT", "/v1/ops/catalog/zone-prices", {
        product_code: "airport_transfer", zone_id: v.zone_id, vehicle_class: v.vehicle_class,
        price: { amount_minor: amount, currency: v.currency },
      });
    }) },
      h("h2", {}, "Set a price"),
      h("div", { class: "row" },
        field("Area", choose("zone_id", zones.map((z) => [z.code, z.name]))),
        field("Vehicle", choose("vehicle_class", (product ? product.vehicle_classes : []).map((c) => [c, c]))),
        field("Price", text("amount", { required: true, inputmode: "decimal", placeholder: "1200.00" })),
        field("Currency", choose("currency", [["ETB", "ETB"], ["USD", "USD"]]))),
      h("div", { class: "actions" }, h("button", { class: "primary", type: "submit" }, "Publish price"))),
    product ? h("div", { class: "card" },
      h("h2", {}, "Current airport transfer terms"),
      h("dl", {},
        h("dt", {}, "Free cancellation"), h("dd", {}, `up to ${product.policy.free_cancel_hours} h before pickup`),
        h("dt", {}, "Late cancellation fee"), h("dd", {}, `${product.policy.late_cancel_fee_pct}% of the fare`),
        h("dt", {}, "Free waiting"), h("dd", {}, `${product.policy.free_wait_minutes} minutes`),
        h("dt", {}, "No-show fee"), h("dd", {}, `${product.policy.no_show_fee_pct}% of the fare`))) : null,
  );
}

// --- Partners -----------------------------------------------------------------

async function renderPartners() {
  const partners = await call("GET", "/v1/ops/partners");
  const reports = await Promise.all(partners.map((p) => call("GET", `/v1/ops/partners/${p.code}/attribution`)));
  const total = (counts) => Object.values(counts).reduce((a, b) => a + b, 0);
  show(
    h("h1", {}, "Partners"),
    h("p", { class: "muted" }, "Each partner's booking link is /book?partner=CODE. Commission is owed on completed trips."),
    partners.length === 0 ? h("p", { class: "muted" }, "No partners yet.") : table(
      ["Code", "Name", "Kind", "Rate", "Bookings", "Completed", "Commission due", "Link", ""],
      partners.map((p, i) => h("tr", {},
        h("td", {}, p.code), h("td", {}, p.name), h("td", {}, p.kind), h("td", {}, `${p.commission_pct}%`),
        h("td", {}, total(reports[i].bookings_by_status)),
        h("td", {}, reports[i].bookings_by_status.completed || 0),
        h("td", {}, reports[i].commission_due.map((m) => money(m)).join(", ") || "—"),
        h("td", {}, p.active ? `${location.origin}/book?partner=${p.code}` : "inactive"),
        h("td", {}, h("button", { class: "link", onclick: busy(() => call("PATCH", `/v1/ops/partners/${p.code}`, { active: !p.active })) }, p.active ? "Deactivate" : "Reactivate"))))),
    h("form", { class: "card", onsubmit: busy(async (event) => {
      const v = formValues(event);
      await call("POST", "/v1/ops/partners", { ...v, commission_pct: Number(v.commission_pct) });
    }) },
      h("h2", {}, "Add a partner"),
      h("div", { class: "row" },
        field("Code", text("code", { required: true, pattern: "[A-Za-z0-9_-]{3,40}", placeholder: "HILTON" })),
        field("Name", text("name", { required: true })),
        field("Kind", choose("kind", [["hotel", "hotel"], ["host", "host"], ["agency", "agency"]])),
        field("Commission %", text("commission_pct", { type: "number", min: 0, max: 100, required: true }))),
      h("div", { class: "actions" }, h("button", { class: "primary", type: "submit" }, "Add partner"))),
  );
}

// --- Audit --------------------------------------------------------------------

async function renderAudit() {
  const entries = await call("GET", "/v1/ops/audit");
  show(
    h("h1", {}, "Audit log"),
    entries.length === 0 ? h("p", { class: "muted" }, "Nothing recorded yet.") : table(
      ["When (Addis)", "Action", "On", "Details"],
      entries.map((e) => h("tr", {},
        h("td", {}, addisTime(e.at)), h("td", {}, e.action),
        h("td", {}, `${e.target_type} ${e.target_type === "booking" ? ref(e.target_id) : e.target_id}`),
        h("td", {}, JSON.stringify(e.details))))),
  );
}

// --- Shell --------------------------------------------------------------------

async function render() {
  if (!state.token) return renderLogin();
  nav.replaceChildren(
    ...Object.entries(TABS).map(([key, label]) => h("button", {
      "aria-current": state.tab === key ? "page" : null,
      onclick: () => { state.tab = key; state.bookingId = null; state.error = null; render(); },
    }, label)),
    h("button", { onclick: signOut }, "Sign out"),
  );
  const pages = { bookings: state.bookingId ? renderBooking : renderBookings, drivers: renderDrivers, prices: renderPrices, partners: renderPartners, audit: renderAudit };
  try {
    await pages[state.tab]();
  } catch (error) {
    if (error.status === 401) return signOut();
    state.error = null;
    show(h("div", { class: "error" }, error.message));
  }
}

render();
