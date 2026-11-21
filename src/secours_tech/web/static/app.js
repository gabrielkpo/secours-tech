// The page of SecoursTech: a chat thread. Each question is answered alone (the server keeps no
// conversation); the session's questions are kept in memory only, listed on the left, and gone
// on reload. Everything the server returns is shown as text (textContent), never parsed as HTML,
// and only https links are made.
"use strict";

const MAX = 500;
const STATUS = {
  answered: "Réponse vérifiée",
  clarification: "Précision demandée",
  refused: "Pas dans la doctrine",
  blocked: "Réponse bloquée",
  error: "Erreur",
};

const $ = (id) => document.getElementById(id);
const form = $("ask");
const field = $("question");
const count = $("count");
const send = $("send");
const thread = $("thread");
const welcome = $("welcome");
const history = $("history");
const historyEmpty = $("history-empty");
const sidebar = $("sidebar");
const scrim = $("scrim");
const menu = $("menu");

const items = []; // {id, question, data, error}; data null while the answer is pending
let shown = []; // ids of the exchanges in the thread
let busy = false;

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function logo() {
  const img = element("img");
  img.src = "/static/logo.svg";
  img.alt = "";
  img.width = 32;
  img.height = 32;
  return img;
}

function link(item) {
  const label = item.title;
  if (!/^https:\/\//.test(item.link)) return element("span", label);
  const anchor = element("a", label);
  anchor.href = item.link;
  anchor.target = "_blank";
  anchor.rel = "noopener noreferrer";
  return anchor;
}

function where(item) {
  return element("span", `v${item.version} · page ${item.page}`, "where");
}

function flash(card) {
  card.scrollIntoView({ behavior: "smooth", block: "nearest" });
  card.classList.add("flash");
  setTimeout(() => card.classList.remove("flash"), 1200);
}

function renderAnswer(item, body) {
  const data = item.data;
  if (item.error) {
    body.append(element("span", STATUS.error, "status error"));
    body.append(element("p", item.error, "message"));
    return;
  }
  body.append(element("span", STATUS[data.status] || data.status, `status ${data.status}`));
  if (data.status === "clarification") body.append(element("p", data.message, "clarify"));
  else if (data.message) body.append(element("p", data.message, "message"));

  const cards = new Map();
  if (data.sentences.length) {
    const list = element("ul", undefined, "sentences");
    for (const sentence of data.sentences) {
      const li = element("li", sentence.text);
      for (const n of sentence.sources) {
        const ref = element("button", String(n), "ref");
        ref.type = "button";
        ref.setAttribute("aria-label", `Voir la source ${n}`);
        ref.addEventListener("click", () => cards.has(n) && flash(cards.get(n)));
        li.append(ref);
      }
      list.append(li);
    }
    body.append(list);
  }
  if (data.sources.length) {
    body.append(element("p", "Sources", "section-title"));
    const list = element("ol", undefined, "sources");
    for (const source of data.sources) {
      const card = element("li", undefined, "source");
      const head = element("div", undefined, "source-head");
      head.append(element("span", String(source.n), "source-n"), link(source), where(source));
      card.append(head);
      if (source.ocr) card.append(element("span", "Lu par OCR : vérifier sur la page", "ocr"));
      if (source.quotes.length) {
        const details = element("details");
        const label = source.quotes.length > 1 ? "extraits cités" : "extrait cité";
        details.append(element("summary", `${source.quotes.length} ${label}`));
        for (const quote of source.quotes) details.append(element("blockquote", `« ${quote} »`));
        card.append(details);
      }
      cards.set(source.n, card);
      list.append(card);
    }
    body.append(list);
  }
  if (data.pages.length) {
    body.append(element("p", "Pages les plus proches", "section-title"));
    const list = element("ul", undefined, "pages");
    for (const page of data.pages) {
      const li = element("li", undefined, "page");
      li.append(link(page), where(page));
      list.append(li);
    }
    body.append(list);
  }
  body.append(element("p", data.notice, "notice"));
}

function renderExchange(item) {
  const section = element("section", undefined, "exchange");
  section.id = `exchange-${item.id}`;
  section.append(element("div", item.question, "question"));
  const answer = element("div", undefined, "answer");
  const body = element("div", undefined, "answer-body");
  answer.append(logo(), body);
  if (item.data === null && !item.error) {
    const waiting = element("div", undefined, "waiting");
    const dots = element("span", undefined, "dots");
    dots.append(element("span"), element("span"), element("span"));
    waiting.append(dots, element("span", "Recherche dans la doctrine et contrôle des citations…"));
    body.append(waiting);
  } else {
    renderAnswer(item, body);
  }
  section.append(answer);
  return section;
}

function renderThread() {
  welcome.hidden = shown.length > 0;
  for (const node of [...thread.querySelectorAll(".exchange")]) node.remove();
  for (const id of shown) thread.append(renderExchange(items[id]));
  renderHistory();
}

// once its answer is in, only that exchange is drawn again: the others keep their state
function update(item) {
  const node = $(`exchange-${item.id}`);
  if (node) node.replaceWith(renderExchange(item));
  renderHistory();
}

function renderHistory() {
  history.replaceChildren();
  for (const item of [...items].reverse()) {
    const li = element("li");
    const button = element("button", item.question);
    button.type = "button";
    button.title = item.question;
    if (shown.length === 1 && shown[0] === item.id) button.className = "current";
    button.addEventListener("click", () => {
      shown = [item.id];
      closeSidebar();
      renderThread();
      thread.scrollTop = 0;
    });
    li.append(button);
    history.append(li);
  }
  historyEmpty.hidden = items.length > 0;
}

function resize() {
  field.style.height = "auto"; // one line when empty, then grows with the text up to 200 px
  if (field.value) field.style.height = `${Math.min(field.scrollHeight, 200)}px`;
  const length = field.value.length;
  count.textContent = length > MAX - 100 ? `${length} / ${MAX}` : "";
  count.className = length >= MAX ? "count full" : "count";
  send.disabled = busy || !field.value.trim();
}

async function ask(question) {
  busy = true;
  const item = { id: items.length, question, data: null, error: "" };
  items.push(item);
  shown.push(item.id);
  field.value = "";
  resize();
  welcome.hidden = true;
  thread.append(renderExchange(item));
  renderHistory();
  thread.scrollTop = thread.scrollHeight;
  try {
    const response = await fetch("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    });
    const data = await response.json().catch(() => ({}));
    if (response.ok) item.data = data;
    else item.error = typeof data.detail === "string" ? data.detail : "La question n'a pas pu être traitée.";
  } catch {
    item.error = "Le service ne répond pas : réessayez plus tard.";
  }
  busy = false;
  resize();
  update(item);
  const node = $(`exchange-${item.id}`);
  if (node) node.scrollIntoView({ behavior: "smooth", block: "start" });
}

function fresh() {
  shown = [];
  closeSidebar();
  renderThread();
  field.focus();
}

function openSidebar() {
  sidebar.classList.add("open");
  scrim.hidden = false;
  menu.setAttribute("aria-expanded", "true");
}

function closeSidebar() {
  sidebar.classList.remove("open");
  scrim.hidden = true;
  menu.setAttribute("aria-expanded", "false");
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const question = field.value.trim();
  if (question && !busy) ask(question);
});
field.addEventListener("input", resize);
field.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    form.requestSubmit();
  }
});
for (const button of $("suggestions").querySelectorAll("button")) {
  button.addEventListener("click", () => !busy && ask(button.textContent.trim()));
}
$("new").addEventListener("click", fresh);
$("new-top").addEventListener("click", fresh);
menu.addEventListener("click", () =>
  sidebar.classList.contains("open") ? closeSidebar() : openSidebar(),
);
scrim.addEventListener("click", closeSidebar);
document.addEventListener("keydown", (event) => event.key === "Escape" && closeSidebar());

resize();
renderThread();
