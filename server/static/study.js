let activeNote = null;
let noteDirty = false;
let studyCards = [];
let reviewCard = null;

function studyMessage(text) {
  el("note-status").textContent = text;
  el("message").textContent = text;
}

function resetStudyEditor() {
  activeNote = null;
  noteDirty = false;
  el("note-form").reset();
  el("note-status").textContent = "";
}

async function loadStudy() {
  const notes = await (await api(`/api/study/notes?q=${encodeURIComponent(el("note-search").value)}`)).json();
  const items = notes.map(note => {
    const button = actionButton(note.title, () => openStudyNote(note.id), "note-link");
    button.setAttribute("aria-pressed", String(note.id === activeNote));
    return button;
  });
  el("notes-list").replaceChildren(...(items.length ? items : [node("p", "No hay notas para mostrar.", "hint")]));
  studyCards = await (await api("/api/study/cards")).json();
  el("review-count").textContent = `${studyCards.filter(card => card.due).length} pendientes · ${studyCards.length} tarjetas`;
}

async function openStudyNote(id) {
  if (noteDirty && !confirm("Hay cambios sin guardar. ¿Abrir otra nota?")) return;
  const note = await (await api(`/api/study/notes/${id}`)).json();
  activeNote = note.id;
  el("note-title").value = note.title;
  el("note-body").value = note.body;
  noteDirty = false;
  studyMessage("Nota abierta.");
  await loadStudy();
}

async function exportStudyFile(path, name) {
  const response = await api(path);
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 60000);
}

el("note-title").oninput = el("note-body").oninput = () => { noteDirty = true; };
let noteSearchTimer;
el("note-search").oninput = () => {
  clearTimeout(noteSearchTimer);
  noteSearchTimer = setTimeout(() => loadStudy().catch(error => studyMessage(error.message)), 250);
};
el("new-note").onclick = () => {
  if (noteDirty && !confirm("Hay cambios sin guardar. ¿Crear una nota nueva?")) return;
  resetStudyEditor();
  el("note-title").focus();
};
el("note-form").onsubmit = async (event) => {
  event.preventDefault();
  el("save-note").disabled = true;
  try {
    const payload = {title:el("note-title").value, body:el("note-body").value};
    if (activeNote) payload.id = activeNote;
    const note = await (await api("/api/study/notes", {method:"POST", body:JSON.stringify(payload)})).json();
    activeNote = note.id;
    noteDirty = false;
    studyMessage("Nota guardada.");
    await loadStudy();
  } catch (error) { studyMessage(error.message); }
  finally { el("save-note").disabled = false; }
};
el("delete-note").onclick = async () => {
  if (!activeNote) return studyMessage("Primero abre una nota guardada.");
  if (!confirm("¿Eliminar la nota y sus tarjetas asociadas?")) return;
  try {
    await api(`/api/study/notes/${activeNote}/delete`, {method:"POST", body:"{}"});
    resetStudyEditor();
    reviewCard = null;
    el("review-card").hidden = true;
    await loadStudy();
    studyMessage("Nota eliminada.");
  } catch (error) { studyMessage(error.message); }
};
el("export-note").onclick = async () => {
  if (!activeNote || noteDirty) return studyMessage("Guarda la nota antes de exportarla.");
  try { await exportStudyFile(`/api/study/notes/${activeNote}/export`, "note.md"); }
  catch (error) { studyMessage(error.message); }
};
el("export-study").onclick = async () => {
  try { await exportStudyFile("/api/study/export", "omniget-study.json"); }
  catch (error) { studyMessage(error.message); }
};
el("card-form").onsubmit = async (event) => {
  event.preventDefault();
  el("save-card").disabled = true;
  try {
    await api("/api/study/cards", {method:"POST", body:JSON.stringify({note_id:activeNote,
      question:el("card-question").value, answer:el("card-answer").value})});
    el("card-form").reset();
    await loadStudy();
    studyMessage("Tarjeta guardada. Ya puedes repasarla.");
  } catch (error) { studyMessage(error.message); }
  finally { el("save-card").disabled = false; }
};

function showNextCard() {
  reviewCard = studyCards.find(card => card.due) || null;
  el("review-card").hidden = !reviewCard;
  if (!reviewCard) return studyMessage("No quedan tarjetas pendientes. ¡Buen trabajo!");
  el("review-question").textContent = reviewCard.question;
  el("review-answer").textContent = reviewCard.answer;
  el("review-answer").hidden = true;
  el("show-answer").hidden = false;
  el("review-ratings").hidden = true;
}
el("start-review").onclick = async () => {
  try { await loadStudy(); showNextCard(); }
  catch (error) { studyMessage(error.message); }
};
el("show-answer").onclick = () => {
  el("review-answer").hidden = false;
  el("show-answer").hidden = true;
  el("review-ratings").hidden = false;
};
for (const rating of ["again", "good", "easy"]) {
  el(`review-${rating}`).onclick = async () => {
    if (!reviewCard) return;
    const id = reviewCard.id;
    for (const button of el("review-ratings").querySelectorAll("button")) button.disabled = true;
    try {
      await api(`/api/study/cards/${id}/review`, {method:"POST", body:JSON.stringify({rating})});
      await loadStudy();
      showNextCard();
    } catch (error) { studyMessage(error.message); }
    finally { for (const button of el("review-ratings").querySelectorAll("button")) button.disabled = false; }
  };
}
el("delete-card").onclick = async () => {
  if (!reviewCard || !confirm("¿Eliminar esta tarjeta?")) return;
  try {
    await api(`/api/study/cards/${reviewCard.id}/delete`, {method:"POST", body:"{}"});
    await loadStudy();
    showNextCard();
  } catch (error) { studyMessage(error.message); }
};
