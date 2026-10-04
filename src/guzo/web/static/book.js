// The booking link: book an airport transfer from any phone browser, no install.
// It only calls the public /v1 API, the same one the app uses.
import { ApiError, addisInputToIso, addisTime, api, field, fill, h, money, ref } from "/static/common.js";

const app = document.getElementById("app");
const TERMINAL = ["completed", "cancelled", "expired", "no_show"];
const POLL_MS = 15000;

const state = {
  lang: localStorage.getItem("guzo.lang") || (navigator.language.startsWith("am") ? "am" : "en"),
  strings: {},
  session: JSON.parse(localStorage.getItem("guzo.session") || "null"),
  partner: null,
  zones: [],
  airport: null,
  trip: { direction: "from", label: "", zone: "", when: "", flight: "", seats: 1, bags: 1 },
  options: [],
  choice: null,
  riders: [{ name: "", phone: "" }],
  iAmRiding: false,
  booker: { name: "", phone: "" },
  idempotencyKey: null,
  afterSignIn: null,
  bookingId: new URLSearchParams(location.search).get("booking"),
  error: null,
  timer: null,
};

function t(key, vars = {}) {
  const text = state.strings[key] || key;
  return text.replace(/\{(\w+)\}/g, (_, name) => vars[name] ?? "");
}

async function loadStrings() {
  state.strings = await (await fetch(`/static/i18n/${state.lang}.json`)).json();
  document.documentElement.lang = state.lang;
  const other = state.lang === "en" ? "am" : "en";
  document.getElementById("lang").replaceChildren(
    h("button", {
      class: "link",
      onclick: async () => {
        state.lang = other;
        localStorage.setItem("guzo.lang", other);
        await loadStrings();
        render();
      },
    }, other === "am" ? "አማርኛ" : "English"),
  );
}

function saveSession(session) {
  state.session = session;
  if (session) localStorage.setItem("guzo.session", JSON.stringify(session));
  else localStorage.removeItem("guzo.session");
}

function show(...nodes) {
  fill(app,
    state.partner ? h("p", { class: "muted" }, t("via_partner", { name: state.partner.name })) : null,
    state.error ? h("div", { class: "error", role: "alert" }, state.error) : null,
    ...nodes,
  );
  window.scrollTo(0, 0);
}

function fail(error) {
  state.error = error instanceof ApiError ? error.message : String(error);
  render();
}

function go(step) {
  state.error = null;
  state.step = step;
  clearTimeout(state.timer);
  render();
}

// Runs an async handler with the button disabled, so a slow network cannot double-submit.
function busy(handler) {
  return async (event) => {
    event.preventDefault();
    const button = event.submitter || event.currentTarget;
    button.disabled = true;
    try {
      await handler();
    } catch (error) {
      fail(error);
    } finally {
      button.disabled = false;
    }
  };
}

function input(id, attrs, onchange) {
  return h("input", { id, ...attrs, oninput: (event) => onchange(event.target.value) });
}

function tripRequest() {
  const { trip } = state;
  const other = { label: trip.label.trim(), kind: "address", zone_id: trip.zone };
  const scheduledAt = addisInputToIso(trip.when);
  return {
    product_code: "airport_transfer",
    pickup: trip.direction === "from" ? state.airport : other,
    dropoff: trip.direction === "from" ? other : state.airport,
    scheduled_at: scheduledAt,
    seats: Number(trip.seats),
    bags: Number(trip.bags),
    flight: trip.flight.trim() ? { number: trip.flight.trim().toUpperCase(), scheduled_arrival: scheduledAt } : null,
  };
}

// --- Step 1: the trip ---------------------------------------------------------

function renderTrip() {
  const { trip } = state;
  const from = trip.direction === "from";
  const direction = h("select", {
    id: "direction",
    onchange: (event) => { trip.direction = event.target.value; render(); },
  },
    h("option", { value: "from", selected: from }, t("from_airport")),
    h("option", { value: "to", selected: !from }, t("to_airport")));
  const zone = h("select", { id: "zone", required: true, onchange: (event) => { trip.zone = event.target.value; } },
    h("option", { value: "" }, "—"),
    state.zones.map((z) => h("option", { value: z.code, selected: z.code === trip.zone }, z.name)));

  show(
    h("h1", {}, t("trip_title")),
    h("p", { class: "muted" }, t("tagline")),
    h("form", {
      class: "card",
      onsubmit: busy(async () => {
        state.options = await api("POST", "/v1/quotes/options", { body: tripRequest() });
        go("options");
      }),
    },
      field(t("direction"), direction),
      field(t(from ? "address_pickup" : "address_dropoff"),
        input("label", { required: true, maxlength: 200, placeholder: t("address_hint"), value: trip.label }, (v) => { trip.label = v; })),
      field(t("zone"), zone),
      field(`${t(from ? "when_pickup" : "when_dropoff")} (${t("addis_time")})`,
        input("when", { type: "datetime-local", required: true, value: trip.when }, (v) => { trip.when = v; })),
      from ? field(t("flight_number"),
        input("flight", { maxlength: 10, placeholder: "ET501", value: trip.flight }, (v) => { trip.flight = v; })) : null,
      h("div", { class: "row" },
        field(t("seats"), input("seats", { type: "number", min: 1, max: 20, required: true, value: trip.seats }, (v) => { trip.seats = v; })),
        field(t("bags"), input("bags", { type: "number", min: 0, max: 30, required: true, value: trip.bags }, (v) => { trip.bags = v; }))),
      h("div", { class: "actions" }, h("button", { class: "primary", type: "submit" }, t("see_prices")))),
  );
}

// --- Step 2: vehicle and price ------------------------------------------------

function renderOptions() {
  show(
    h("h1", {}, t("options_title")),
    state.options.length === 0 ? h("div", { class: "card" }, t("no_options")) : null,
    state.options.map((option) =>
      h("div", { class: "card option" },
        h("div", {},
          h("strong", {}, t(option.vehicle_class)),
          option.max_seats ? h("div", { class: "muted" }, t("up_to", { seats: option.max_seats, bags: option.max_bags })) : null,
          h("div", { class: "price" }, money(option.price, state.lang))),
        h("button", { class: "primary", onclick: () => { state.choice = option; go("details"); } }, t("choose")))),
    state.options.length ? h("p", { class: "muted" }, t("fixed_price")) : null,
    h("div", { class: "actions" }, h("button", { onclick: () => go("trip") }, t("back"))),
  );
}

// --- Step 3: riders and the booker --------------------------------------------

function ridersForBooking() {
  const riders = state.riders
    .filter((r) => r.name.trim() || r.phone.trim())
    .map((r) => ({ name: r.name.trim(), phone: r.phone.trim() }));
  if (state.iAmRiding) {
    riders.push({
      name: state.booker.name.trim() || state.session.user.name || state.session.user.phone,
      phone: state.session.user.phone,
      is_booker: true,
    });
  }
  return riders;
}

function renderDetails() {
  const signedIn = Boolean(state.session);
  const riderRows = state.riders.map((rider, index) =>
    h("div", { class: "row" },
      field(t("rider_name"), input(`rider-name-${index}`, { maxlength: 120, value: rider.name }, (v) => { rider.name = v; })),
      field(t("rider_phone"), input(`rider-phone-${index}`, { type: "tel", placeholder: "+251 9…", value: rider.phone }, (v) => { rider.phone = v; })),
      state.riders.length > 1
        ? h("button", { type: "button", class: "link", onclick: () => { state.riders.splice(index, 1); render(); } }, t("remove"))
        : null));

  show(
    h("h1", {}, t("riders_title")),
    h("p", { class: "muted" }, t("riders_help")),
    h("form", {
      onsubmit: busy(async () => {
        const listed = state.riders.filter((r) => r.name.trim() || r.phone.trim()).length + (state.iAmRiding ? 1 : 0);
        if (listed === 0) throw new ApiError(0, "riders", t("need_rider"));
        if (listed > Number(state.trip.seats)) throw new ApiError(0, "riders", t("too_many_riders"));
        if (signedIn) return submitBooking();
        state.afterSignIn = submitBooking;
        await api("POST", "/v1/auth/otp/request", { body: { phone: state.booker.phone } });
        go("code");
      }),
    },
      h("div", { class: "card" },
        riderRows,
        state.riders.length < Number(state.trip.seats)
          ? h("button", { type: "button", class: "link", onclick: () => { state.riders.push({ name: "", phone: "" }); render(); } }, t("add_rider"))
          : null,
        h("label", { class: "check" },
          h("input", { type: "checkbox", checked: state.iAmRiding, onchange: (event) => { state.iAmRiding = event.target.checked; } }),
          t("i_am_riding"))),
      h("h2", {}, t("booker_title")),
      h("div", { class: "card" },
        h("p", { class: "muted" }, t("booker_help")),
        field(t("your_name"), input("booker-name", { required: !signedIn, maxlength: 120, autocomplete: "name", value: state.booker.name || (signedIn && state.session.user.name) || "" }, (v) => { state.booker.name = v; })),
        signedIn
          ? h("p", {}, state.session.user.phone)
          : field(t("your_phone"), input("booker-phone", { type: "tel", required: true, autocomplete: "tel", placeholder: "+1 415 555 2671", value: state.booker.phone }, (v) => { state.booker.phone = v; }))),
      h("div", { class: "card" },
        h("h2", {}, t("summary")),
        h("dl", {},
          h("dt", {}, t("vehicle")), h("dd", {}, t(state.choice.vehicle_class)),
          h("dt", {}, t("price")), h("dd", { class: "price" }, money(state.choice.price, state.lang)))),
      h("div", { class: "actions" },
        h("button", { type: "button", onclick: () => go("options") }, t("back")),
        h("button", { class: "primary", type: "submit" }, signedIn ? t("verify") : t("send_code")))),
  );
}

// --- Sign-in code -------------------------------------------------------------

function renderCode() {
  let code = "";
  show(
    h("h1", {}, t("code_title")),
    h("form", {
      class: "card",
      onsubmit: busy(async () => {
        const result = await api("POST", "/v1/auth/otp/verify", {
          body: { phone: state.booker.phone, code, name: state.booker.name.trim() || null },
        });
        saveSession({ token: result.access_token, user: result.user });
        await state.afterSignIn();
      }),
    },
      h("p", { class: "muted" }, t("code_help", { phone: state.booker.phone })),
      field(t("code"), input("code", { required: true, inputmode: "numeric", pattern: "\\d{6}", maxlength: 6, autocomplete: "one-time-code" }, (v) => { code = v; })),
      h("div", { class: "actions" },
        h("button", { type: "button", onclick: () => go(state.bookingId ? "signin" : "details") }, t("back")),
        h("button", { class: "primary", type: "submit" }, t("verify")))),
  );
}

// Returning to a booking link on a new device: sign in first.
function renderSignIn() {
  show(
    h("h1", {}, t("booker_title")),
    h("form", {
      class: "card",
      onsubmit: busy(async () => {
        state.afterSignIn = async () => go("status");
        await api("POST", "/v1/auth/otp/request", { body: { phone: state.booker.phone } });
        go("code");
      }),
    },
      field(t("your_phone"), input("booker-phone", { type: "tel", required: true, autocomplete: "tel", placeholder: "+1 415 555 2671", value: state.booker.phone }, (v) => { state.booker.phone = v; })),
      h("div", { class: "actions" }, h("button", { class: "primary", type: "submit" }, t("send_code")))),
  );
}

// --- Create the booking -------------------------------------------------------

async function submitBooking() {
  const token = state.session.token;
  // One key for the whole attempt: a retry after a dropped connection finds the
  // booking already made instead of making a second one.
  state.idempotencyKey = state.idempotencyKey || crypto.randomUUID();
  const quote = await api("POST", "/v1/quotes", {
    token,
    body: { ...tripRequest(), vehicle_class: state.choice.vehicle_class },
  });
  const booking = await api("POST", "/v1/bookings", {
    token,
    headers: { "Idempotency-Key": state.idempotencyKey },
    body: { quote_id: quote.id, riders: ridersForBooking(), partner_code: state.partner ? state.partner.code : null },
  });
  state.bookingId = booking.id;
  state.idempotencyKey = null;
  history.replaceState(null, "", `/book?booking=${booking.id}`);
  await api("POST", `/v1/bookings/${booking.id}/confirm`, { token });
  go("status");
}

// --- Status -------------------------------------------------------------------

async function loadStatus() {
  const token = state.session.token;
  const cacheKey = `guzo.booking.${state.bookingId}`;
  try {
    const booking = await api("GET", `/v1/bookings/${state.bookingId}`, { token });
    const view = { booking, driver: null, payment: null };
    if (booking.assignment) {
      view.driver = await api("GET", `/v1/bookings/${booking.id}/driver`, { token }).catch(() => null);
    }
    if (booking.status === "awaiting_payment") {
      view.payment = (await api("POST", `/v1/bookings/${booking.id}/confirm`, { token })).payment;
    }
    localStorage.setItem(cacheKey, JSON.stringify(view));
    return { ...view, stale: false };
  } catch (error) {
    if (error.status === 401) {
      saveSession(null);
      throw error;
    }
    const cached = JSON.parse(localStorage.getItem(cacheKey) || "null");
    if (error.code === "offline" && cached) return { ...cached, stale: true };
    throw error;
  }
}

function checkoutLink(payment) {
  const url = new URL(payment.checkout_url, location.origin);
  // The test checkout page needs to know where to send the booker back to.
  if (url.origin === location.origin) url.searchParams.set("return", `/book?booking=${state.bookingId}`);
  return url.href;
}

async function renderStatus() {
  if (!state.session) return go("signin");
  let view;
  try {
    view = await loadStatus();
  } catch (error) {
    if (error.status === 401) return go("signin");
    return show(h("div", { class: "error" }, error.message), h("button", { onclick: render }, t("refresh")));
  }
  const { booking, driver, payment, stale } = view;
  let confirmingCancel = false;

  const paint = () => show(
    stale ? h("div", { class: "error" }, t("last_known")) : null,
    h("h1", {}, t("status_title", { ref: ref(booking.id) })),
    h("p", {}, h("span", { class: "status" }, t(`status_${booking.status}`))),
    booking.status === "awaiting_payment" && payment && payment.checkout_url
      ? h("div", { class: "card" },
          h("h2", {}, t("pay_title")),
          h("p", { class: "muted" }, t("pay_help", { time: addisTime(booking.payment_expires_at, state.lang) })),
          h("p", { class: "price" }, money(booking.price, state.lang)),
          h("div", { class: "actions" }, h("a", { class: "button primary", href: checkoutLink(payment) }, t("pay_now"))))
      : null,
    driver
      ? h("div", { class: "card" },
          h("h2", {}, t("driver")),
          h("div", { class: "row" },
            driver.photo_url ? h("img", { class: "driver", src: driver.photo_url, alt: "" }) : null,
            h("dl", {},
              h("dt", {}, t("driver")), h("dd", {}, driver.name || "—", driver.phone ? ` · ${driver.phone}` : ""),
              driver.vehicle ? [h("dt", {}, t("car")), h("dd", {}, `${driver.vehicle.color} ${driver.vehicle.make} ${driver.vehicle.model} · ${driver.vehicle.plate}`)] : null)))
      : null,
    h("div", { class: "card" },
      h("dl", {},
        h("dt", {}, t("pickup")), h("dd", {}, booking.pickup.label),
        h("dt", {}, t("dropoff")), h("dd", {}, booking.dropoff.label),
        h("dt", {}, t("time")), h("dd", {}, `${addisTime(booking.scheduled_at, state.lang)} (${t("addis_time")})`),
        h("dt", {}, t("vehicle")), h("dd", {}, t(booking.vehicle_class)),
        h("dt", {}, t("riders")), h("dd", {}, booking.riders.map((r) => r.name).join(", ")),
        h("dt", {}, t("price")), h("dd", {}, money(booking.price, state.lang)))),
    ["confirmed", "assigned"].includes(booking.status) && !stale
      ? h("div", { class: "card" },
          h("p", { class: "muted" }, t("cancel_help", { hours: booking.policy.free_cancel_hours })),
          confirmingCancel
            ? h("div", { class: "actions" },
                h("button", { class: "danger", onclick: busy(async () => {
                  await api("POST", `/v1/bookings/${booking.id}/cancel`, { token: state.session.token, body: {} });
                  render();
                }) }, t("cancel")),
                h("button", { onclick: () => { confirmingCancel = false; paint(); } }, t("back")))
            : h("button", { class: "danger", onclick: () => { confirmingCancel = true; paint(); } }, t("cancel")))
      : null,
    h("div", { class: "actions" },
      h("button", { onclick: render }, t("refresh")),
      TERMINAL.includes(booking.status) ? h("a", { class: "button", href: "/book" }, t("new_booking")) : null),
  );
  paint();

  clearTimeout(state.timer);
  if (!TERMINAL.includes(booking.status)) {
    state.timer = setTimeout(() => { if (state.step === "status" && !confirmingCancel) render(); }, POLL_MS);
  }
}

// --- Start --------------------------------------------------------------------

function render() {
  const steps = { trip: renderTrip, options: renderOptions, details: renderDetails, code: renderCode, signin: renderSignIn, status: renderStatus };
  steps[state.step]();
}

async function start() {
  await loadStrings();
  const code = new URLSearchParams(location.search).get("partner");
  const [zones, places, partner] = await Promise.all([
    api("GET", "/v1/catalog/zones"),
    api("GET", "/v1/catalog/places"),
    code ? api("GET", `/v1/partners/${encodeURIComponent(code)}`).catch(() => null) : null,
  ]);
  state.zones = zones;
  state.airport = places.find((p) => p.place.kind === "airport").place;
  state.partner = partner;
  state.step = state.bookingId ? "status" : "trip";
  render();
}

start().catch((error) => show(h("div", { class: "error" }, error.message)));
