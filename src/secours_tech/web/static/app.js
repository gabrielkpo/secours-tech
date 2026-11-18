// The page of SecoursTech: sends the question, shows the checked answer. Everything the server
// returns is shown as text (textContent), never parsed as HTML, and only https links are made.
"use strict";

const form = document.getElementById("ask");
const field = document.getElementById("question");
const count = document.getElementById("count");
const send = document.getElementById("send");
const result = document.getElementById("result");

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function link(item) {
  const label = `${item.title} v${item.version}, page ${item.page}`;
  if (!/^https:\/\//.test(item.link)) return element("span", label);
  const anchor = element("a", label);
  anchor.href = item.link;
  anchor.target = "_blank";
  anchor.rel = "noopener noreferrer";
  return anchor;
}

function show(answer) {
  result.replaceChildren();
  if (answer.message) result.append(element("p", answer.message, `message ${answer.status}`));
  if (answer.sentences.length) {
    const list = element("ul", undefined, "sentences");
    for (const sentence of answer.sentences) {
      const refs = sentence.sources.map((n) => `[${n}]`).join("");
      list.append(element("li", `${sentence.text} ${refs}`));
    }
    result.append(list);
  }
  if (answer.sources.length) {
    result.append(element("h2", "Sources"));
    const list = element("ol", undefined, "sources");
    for (const source of answer.sources) {
      const item = element("li");
      item.append(link(source));
      if (source.ocr) item.append(element("span", " (lu par OCR : vérifier sur la page)", "ocr"));
      for (const quote of source.quotes) item.append(element("blockquote", `« ${quote} »`));
      list.append(item);
    }
    result.append(list);
  }
  if (answer.pages.length) {
    result.append(element("h2", "Pages les plus proches"));
    const list = element("ul", undefined, "pages");
    for (const page of answer.pages) {
      const item = element("li");
      item.append(link(page));
      list.append(item);
    }
    result.append(list);
  }
  result.append(element("p", answer.notice, "notice"));
}

function fail(text) {
  result.replaceChildren(element("p", text, "message error"));
}

field.addEventListener("input", () => {
  count.textContent = `${field.value.length} / 500`;
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const question = field.value.trim();
  if (!question) return;
  send.disabled = true;
  result.replaceChildren(element("p", "Recherche dans la doctrine et contrôle des citations…", "wait"));
  try {
    const response = await fetch("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    });
    const data = await response.json().catch(() => ({}));
    if (response.ok) show(data);
    else fail(typeof data.detail === "string" ? data.detail : "La question n'a pas pu être traitée.");
  } catch {
    fail("Le service ne répond pas : réessayez plus tard.");
  } finally {
    send.disabled = false;
  }
});
